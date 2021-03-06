import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import Dataset
from torch.utils.data import DataLoader

import matplotlib.pyplot as plt
from matplotlib.pyplot import figure
import plotly.graph_objs as go
import plotly.express as px

from alpha_vantage.timeseries import TimeSeries
from alpha_vantage.cryptocurrencies import CryptoCurrencies

def get_lstm_plot_data():

    # ! Long short-term memory (LSTM) deep learning algorithm is a specialized architecture that can "memorize" patterns from historical sequences of data and extrapolate such patterns for future events. Here I try to use it to predict BTC & ETH's price. 

    config = {
        "alpha_vantage": {
            "key": "your-API-key", # Claim your free API key here: https://www.alphavantage.co/support/#api-key
            "symbol": "ETH",
            "market": "USD",
            "key_adjusted_close": "4b. close (USD)",
        },
        "data": {
            "window_size": 20,
            "train_split_size": 0.80,
        },
        "plots": {
            "xticks_interval": 90, # show a date every 90 days
            "color_actual": "#001f3f",
            "color_train": "#3D9970",
            "color_val": "#0074D9",
            "color_pred_train": "#3D9970",
            "color_pred_val": "#0074D9",
            "color_pred_test": "#FF4136",
        },
        "model": {
            "input_size": 1, # since we are only using 1 feature, close price
            "num_lstm_layers": 2,
            "lstm_size": 32,
            "dropout": 0.2,
        },
        "training": {
            "device": "cpu", # "cuda" or "cpu"
            "batch_size": 64,
            "num_epoch": 100,
            "learning_rate": 0.01,
            "scheduler_step_size": 40,
        }
    }

    # ! We need historical stock price data to train our deep learning model
    # ? We use the adjusted closing price here - as that is the "best practice"

    def download_data(config):
        cc = CryptoCurrencies(key=config["alpha_vantage"]["key"])
        data, meta_data = cc.get_digital_currency_daily(symbol=config["alpha_vantage"]["symbol"], market=config["alpha_vantage"]["market"])

        data_date = [date for date in data.keys()]
        data_date.reverse()

        data_close_price = [float(data[date][config["alpha_vantage"]["key_adjusted_close"]]) for date in data.keys()]
        data_close_price.reverse()
        data_close_price = np.array(data_close_price)

        num_data_points = len(data_date)
        display_date_range = "from " + data_date[0] + " to " + data_date[num_data_points-1]

        return data_date, data_close_price, num_data_points, display_date_range

    data_date, data_close_price, num_data_points, display_date_range = download_data(config)


    # ! Data normalization can increase the accuracy of the model and help the "gradient descent algorithm"(LSTM algorithm) converge more quickly. 

    class Normalizer():
        def __init__(self):
            self.mu = None
            self.sd = None

        def fit_transform(self, x):
            self.mu = np.mean(x, axis=(0), keepdims=True)
            self.sd = np.std(x, axis=(0), keepdims=True)
            normalized_x = (x - self.mu)/self.sd
            return normalized_x

        def inverse_transform(self, x):
            return (x*self.sd) + self.mu

    scaler = Normalizer()
    normalized_data_close_price = scaler.fit_transform(data_close_price)

    # ! Predict the 21st day's close price based on the past 20 days' close price
    # ? Why choose 20 days? 
    # ? - When LSTM models are used in NLP, the number of words in a sentence typically ranges from 15 to 20 words
    # ? - Gradient descent considerations: attempting to back-propagate across very long input sequences may result in vanishing gradients
    # ? - Longer sequences tend to have much longer training times

    def prepare_data_x(x, window_size): # windowing

        n_row = x.shape[0] - window_size + 1
        output = np.lib.stride_tricks.as_strided(x, shape=(n_row, window_size), strides=(x.strides[0], x.strides[0]))
        return output[:-1], output[-1]


    def prepare_data_y(x, window_size): # simple moving average

        output = x[window_size:]
        return output

    data_x, data_x_unseen = prepare_data_x(normalized_data_close_price, window_size=config["data"]["window_size"])
    data_y = prepare_data_y(normalized_data_close_price, window_size=config["data"]["window_size"])

    split_index = int(data_y.shape[0]*config["data"]["train_split_size"])
    data_x_train = data_x[:split_index]
    data_x_val = data_x[split_index:]
    data_y_train = data_y[:split_index]
    data_y_val = data_y[split_index:]


    # ! Implement the data loader functionality

    class TimeSeriesDataset(Dataset):
        def __init__(self, x, y):
            x = np.expand_dims(x,
                            2)  # in our case, we have only 1 feature, so we need to convert `x` into [batch, sequence, features] for LSTM
            self.x = x.astype(np.float32)
            self.y = y.astype(np.float32)

        def __len__(self):
            return len(self.x)

        def __getitem__(self, idx):
            return (self.x[idx], self.y[idx])


    dataset_train = TimeSeriesDataset(data_x_train, data_y_train)
    dataset_val = TimeSeriesDataset(data_x_val, data_y_val)

    train_dataloader = DataLoader(dataset_train, batch_size=config["training"]["batch_size"], shuffle=True)
    val_dataloader = DataLoader(dataset_val, batch_size=config["training"]["batch_size"], shuffle=True)

    # ! Define 3 layers for our LSTM neural network & Randomly "dropout"/ignore some neurons during training to prevent overfitting
    # ! 1) To map input values into a high dimensional feature space
    # ! 2) To learn the data in sequence 
    # ! 3) To produce the predicted value based on LSTM's output 

    class LSTMModel(nn.Module):
        def __init__(self, input_size=1, hidden_layer_size=32, num_layers=2, output_size=1, dropout=0.2):
            super().__init__()
            self.hidden_layer_size = hidden_layer_size

            self.linear_1 = nn.Linear(input_size, hidden_layer_size)
            self.relu = nn.ReLU()
            self.lstm = nn.LSTM(hidden_layer_size, hidden_size=self.hidden_layer_size, num_layers=num_layers,
                                batch_first=True)
            self.dropout = nn.Dropout(dropout)
            self.linear_2 = nn.Linear(num_layers * hidden_layer_size, output_size)

            self.init_weights()

        def init_weights(self):
            for name, param in self.lstm.named_parameters():
                if 'bias' in name:
                    nn.init.constant_(param, 0.0)
                elif 'weight_ih' in name:
                    nn.init.kaiming_normal_(param)
                elif 'weight_hh' in name:
                    nn.init.orthogonal_(param)

        def forward(self, x):
            batchsize = x.shape[0]

            # layer 1
            x = self.linear_1(x)
            x = self.relu(x)

            # LSTM layer
            lstm_out, (h_n, c_n) = self.lstm(x)

            # reshape output from hidden cell into [batch, features] for `linear_2`
            x = h_n.permute(1, 0, 2).reshape(batchsize, -1)

            # layer 2
            x = self.dropout(x)
            predictions = self.linear_2(x)
            return predictions[:, -1]

    # ! Start the model training process

    def run_epoch(dataloader, is_training=False):
        epoch_loss = 0

        if is_training:
            model.train()
        else:
            model.eval()

        for idx, (x, y) in enumerate(dataloader):
            if is_training:
                optimizer.zero_grad()

            batchsize = x.shape[0]

            x = x.to(config["training"]["device"])
            y = y.to(config["training"]["device"])

            out = model(x)
            loss = criterion(out.contiguous(), y.contiguous())

            if is_training:
                loss.backward()
                optimizer.step()

            epoch_loss += (loss.detach().item() / batchsize)

        lr = scheduler.get_last_lr()[0]

        return epoch_loss, lr


    train_dataloader = DataLoader(dataset_train, batch_size=config["training"]["batch_size"], shuffle=True)
    val_dataloader = DataLoader(dataset_val, batch_size=config["training"]["batch_size"], shuffle=True)

    model = LSTMModel(input_size=config["model"]["input_size"], hidden_layer_size=config["model"]["lstm_size"],
                    num_layers=config["model"]["num_lstm_layers"], output_size=1, dropout=config["model"]["dropout"])
    model = model.to(config["training"]["device"])

    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=config["training"]["learning_rate"], betas=(0.9, 0.98), eps=1e-9)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=config["training"]["scheduler_step_size"], gamma=0.1)

    for epoch in range(config["training"]["num_epoch"]):
        loss_train, lr_train = run_epoch(train_dataloader, is_training=True)
        loss_val, lr_val = run_epoch(val_dataloader)
        scheduler.step()

    # here we re-initialize dataloader so the data doesn't shuffled, so we can plot the values by date

    train_dataloader = DataLoader(dataset_train, batch_size=config["training"]["batch_size"], shuffle=False)
    val_dataloader = DataLoader(dataset_val, batch_size=config["training"]["batch_size"], shuffle=False)

    # ! Load the model and start predicting ^^

    model.eval()

    # ! predict on the training data, to see how well the model managed to learn and memorize

    predicted_train = np.array([])

    for idx, (x, y) in enumerate(train_dataloader):
        x = x.to(config["training"]["device"])
        out = model(x)
        out = out.cpu().detach().numpy()
        predicted_train = np.concatenate((predicted_train, out))

    # ! predict on the validation data, to see how the model does

    predicted_val = np.array([])

    for idx, (x, y) in enumerate(val_dataloader):
        x = x.to(config["training"]["device"])
        out = model(x)
        out = out.cpu().detach().numpy()
        predicted_val = np.concatenate((predicted_val, out))

    # ! predict the closing price of the next trading day

    model.eval()

    x = torch.tensor(data_x_unseen).float().to(config["training"]["device"]).unsqueeze(0).unsqueeze(2) # this is the data type and shape required, [batch, sequence, feature]
    prediction = model(x)
    prediction = prediction.cpu().detach().numpy()

    # ! prepare plots

    plot_range = 10
    to_plot_data_y_val = np.zeros(plot_range)
    to_plot_data_y_val_pred = np.zeros(plot_range)
    to_plot_data_y_test_pred = np.zeros(plot_range)

    to_plot_data_y_val[:plot_range-1] = scaler.inverse_transform(data_y_val)[-plot_range+1:]
    to_plot_data_y_val_pred[:plot_range-1] = scaler.inverse_transform(predicted_val)[-plot_range+1:]
    to_plot_data_y_test_pred[plot_range-1] = scaler.inverse_transform(prediction)

    to_plot_data_y_val = np.where(to_plot_data_y_val == 0, None, to_plot_data_y_val)
    to_plot_data_y_val_pred = np.where(to_plot_data_y_val_pred == 0, None, to_plot_data_y_val_pred)
    to_plot_data_y_test_pred = np.where(to_plot_data_y_test_pred == 0, None, to_plot_data_y_test_pred)

    # ! plot

    plot_date_test = data_date[-plot_range+1:]
    plot_date_test.append("tomorrow")
    
    fig = go.Figure()

    # Set up traces
    fig.add_trace(go.Scatter(x=plot_date_test, y= to_plot_data_y_val,line=dict(color='royalblue', width=.8), name = 'Actual prices'))
    fig.add_trace(go.Scatter(x=plot_date_test, y= to_plot_data_y_val_pred,line=dict(color='darkorange', width=.8), name = 'Past predicted prices'))
    fig.add_trace(go.Scatter(x=plot_date_test, y= to_plot_data_y_test_pred, mode='markers', marker_color='red', marker_symbol='diamond', name = 'Predicted price for next day'))
    fig.update_xaxes(type='category')

    #Show
    fig.update_layout(autosize=True, title_text="Predicting the close price of the next trading day", legend=dict(
    yanchor="top",
    y=0.99,
    xanchor="left",
    x=0.01
    ))

    return fig
