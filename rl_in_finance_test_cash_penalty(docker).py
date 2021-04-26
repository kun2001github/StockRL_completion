# -*- coding: utf-8 -*-
"""RL_in_Finance_Test_Cash_Penalty(Docker).ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1cK3Kgh-4lPpqLjMG6m-ULTZvFNhunK5-

# RL in Finance(Test Cash Penalty) 
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/sunnyswag/RL_in_Finance/blob/main/RL_in_Finance_Test_cash_penalty.ipynb)

## 1、拉取 github 仓库，下载并导入相关包
&emsp;&emsp;运行流程：python setup.py -> pip install -r requirements.txt
"""

# Commented out IPython magic to ensure Python compatibility.
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import datetime
import time

# %matplotlib inline
from utils import config
from utils.pull_data import Pull_data
from utils.preprocessors import FeatureEngineer, split_data
from utils.env_cash_penalty import StockTradingEnvCashpenalty
from utils.models import DRL_Agent
from utils.backtest import backtest_stats, backtest_plot, get_baseline
import itertools
import sys
sys.path.append("../RL_in_Finance")

"""## 2、下载数据

数据来源：Tushare API<br>
当前用到的数据：SSE_50 和 CSI_300<br>
数据量的大小：shape[2892 * n, 8]
"""

stock_list = config.SSE_50
df = Pull_data(stock_list, save_data=False).pull_data()

df.sort_values(['date', 'tic'], ignore_index=True).head()

print("数据下载的时间区间为：{} 至 {}".format(config.Start_Date, config.End_Date))

print("下载的股票列表为: ")
print(stock_list)

"""## 3、数据预处理"""

processed_df = FeatureEngineer(use_technical_indicator=True).preprocess_data(df)
processed['log_volume'] = np.log(processed.volume*processed.close)
processed['change'] = (processed.close-processed.open)/processed.close
processed['daily_variance'] = (processed.high-processed.low)/processed.close

print("技术指标列表: ")
print(config.TECHNICAL_INDICATORS_LIST)
print("技术指标数: {}个".format(len(config.TECHNICAL_INDICATORS_LIST)))

processed_df.head()

train_data = split_data(processed_df, config.Start_Trade_Date, config.End_Trade_Date)
test_data = split_data(processed_df, config.End_Trade_Date, config.End_Test_Date)

print("训练数据的范围：{} 至 {}".format(config.Start_Trade_Date, config.End_Trade_Date))
print("测试数据的范围：{} 至 {}".format(config.End_Trade_Date, config.End_Test_Date))
print("训练数据的长度: {},测试数据的长度:{}".format(len(train_data), len(test_data)))
print("训练集数据 : 测试集数据: {} : {}".format(round(len(train_data)/len(test_data),1), 1))

train_data.head()

test_data.head()

"""## 4、初始化环境

**state_space 由四部分组成 :** <br>
1. 当天的资金量
2. 每只股票当天的收盘价
3. 每只股票当天的持仓量
4. 股票数 * 技术指标数<br>
5. 当天成交量

**reward 的计算方式：**<br>
* reward 交易前的总资产-当天交易后的总资产 = 当天交易的手续费
* TODO：待改进

**action_space 的空间：**<br>
  * actions ∈[-100, 100]
  * 正数表示买入，负数表示卖出，0表示不进行买入卖出操作
  * 绝对值表示买入卖出的数量
"""

# stock_dimension = len(df.tic.unique())
# state_space = 1 + 2*stock_dimension + \
#     len(config.TECHNICAL_INDICATORS_LIST)*stock_dimension + stock_dimension
# print("stock_dimension: {}, state_space: {}".format(stock_dimension, state_space))

# 初始化环境的参数
information_cols = config.TECHNICAL_INDICATORS_LIST + ["close", "day", "log_volume", "change", "daily_variance"]

e_train_gym = StockTradingEnvCashpenalty(df = train_data,initial_amount = 1e6,hmax = 5000, 
                                turbulence_threshold = None, 
                                currency='￥',
                                buy_cost_pct=3e-3,
                                sell_cost_pct=3e-3,
                                cash_penalty_proportion=0.2,
                                cache_indicator_data=True,
                                daily_information_cols = information_cols, 
                                print_verbosity = 500, 
                                random_start = True)

e_trade_gym = StockTradingEnvCashpenalty(df = test_data,initial_amount = 1e6,hmax = 5000, 
                                turbulence_threshold = None, 
                                currency='￥',
                                buy_cost_pct=3e-3,
                                sell_cost_pct=3e-3,
                                cash_penalty_proportion=0.2,
                                cache_indicator_data=True,
                                daily_information_cols = information_cols, 
                                print_verbosity = 500, 
                                random_start = False)

# 对环境进行测试
observation = e_train_gym.reset() # 初始化环境，observation为环境状态
count = 0
total_reward = 0
for t in range(100):
  action = e_train_gym.action_space.sample() # 随机采样动作
  observation, reward, done, info = e_train_gym.step(action) # 与环境交互，获得下一个state的值
  total_reward += reward
  if done:             
      break
  count+=1
  # time.sleep(0.2)      #每次等待 0.2s
print("count: ", count)
print("observation: ", observation)
print("reward: {}, done: {}".format(total_reward, done))

import multiprocessing

n_cores = multiprocessing.cpu_count() - 2
print("using {} cores".format(n_cores))

#this is our training env. It allows multiprocessing
env_train, _ = e_train_gym.get_multiproc_env(n = n_cores)

#this is our observation environment. It allows full diagnostics
env_trade, _ = e_trade_gym.get_sb_env()

"""## 5、开始训练

所用到的框架：stable_baseline3
"""

agent = DRLAgent(env = env_train)

# from torch.nn import Softsign, ReLU
ppo_params ={'n_steps': 256, 
             'ent_coef': 0.0, 
             'learning_rate': 0.000005, 
             'batch_size': 256, 
            'gamma': 0.99}

policy_kwargs = {
#     "activation_fn": ReLU,
    "net_arch": [1024 for _ in range(10)], 
#     "squash_output": True
}

model = agent.get_model("ppo",  
                        model_kwargs = ppo_params, 
                        policy_kwargs = policy_kwargs, verbose = 0)

# model = model.load("scaling_reward.model", env = env_train)

model.learn(total_timesteps = 50000, 
            eval_env = env_trade, 
            eval_freq = 500,
            log_interval = 1, 
            tb_log_name = 'env_cashpenalty_highlr',
            n_eval_episodes = 1)

model.save("scaling_reward.model")

"""## 6、测试"""

df_account_value, df_actions = DRL_prediction(
    model=model, 
    environment = e_trade_gym)

print("回测的时间窗口：{} 至 {}".format(config.End_Trade_Date, config.End_Test_Date))

df_account_value.to_csv("df_account_value.csv", index=False)
print("查看日账户净值")
print("开始: ")
print(df_account_value.head())
print("")
print("结束: ")
print(df_account_value.tail())

print("查看每日所作的交易")
df_actions.to_csv("df_actions.csv", index=False)
df_actions.tail()

"""## 7、回测"""

print("---------------------获取回测结果---------------------")
pref_stats_all = backtest_stats(account_value=df_account_value)

# perf_stats_all = pd.DataFrame(perf_stats_all)
# now = datetime.datetime.now().strftime('%Y%m%d-%Hh%M')
# perf_stats_all.to_csv("./"+config.RESULTS_DIR+"/perf_stats_all_"+now+'.csv')

# 获取 baseline 的结果
print("---------------------获取baseline结果---------------------")
baseline_df = get_baseline(config.SSE_50_INDEX, 
              start="20190101",
              end="20210101")
baseline_stats = backtest_stats(baseline_df, value_col_name='close')

# 删除 df_account_value 中重复的行
df_account_value.drop(df_account_value.index[1], inplace=True)

baseline_df.head(10)

# Commented out IPython magic to ensure Python compatibility.
print("---------------------Plot---------------------")
print("和 {} 指数进行比较".format(config.SSE_50_INDEX[0]))
# %matplotlib inline
backtest_plot(df_account_value,
        baseline_start="20190101",
        baseline_end="20210101",
        baseline_ticker=config.SSE_50_INDEX,
      )

