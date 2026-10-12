# 安装与配置

适用于所有机器人以及所有机器（PC 与树莓派）。

## 安装 conda

**x86-64（PC / Ubuntu）：**

```bash
mkdir -p ~/miniconda3
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh
bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3
rm ~/miniconda3/miniconda.sh
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

**ARM64（树莓派）：**

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh \
  -O ~/miniforge3/miniforge.sh
bash ~/miniforge3/miniforge.sh -b -u -p ~/miniforge3
rm ~/miniforge3/miniforge.sh
~/miniforge3/bin/conda init bash
source ~/.bashrc
```

## 克隆并安装

```bash
git clone https://github.com/liyiteng/lerobot_alohamini.git
cd lerobot_alohamini
conda create -y -n lerobot_alohamini python=3.12
conda activate lerobot_alohamini
pip install -e ".[all]"
pip install pyzmq feetech-servo-sdk
conda install -y ffmpeg=7.1.1 -c conda-forge
```

## 串口权限

一次性设置 —— 将你的用户加入 `dialout` 组，然后重启：

```bash
sudo usermod -a -G dialout $USER
# 重启后该改动才会生效
```

## HuggingFace 配置

在 [huggingface.co](https://huggingface.co) 注册账号，生成一个具有读写（read + write）权限的 token，然后登录：

```bash
git config --global credential.helper store
hf auth login --token <your_token> --add-to-git-credential
```

获取你的用户名（本指南中所有数据集路径都会用到它）：

```bash
HF_USER=$(hf auth whoami | sed 's/^user=//')
echo $HF_USER
```
