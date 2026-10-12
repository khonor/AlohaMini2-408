# lerobot_alohamini

AlohaMini 产品线的共享软件层，基于 HuggingFace LeRobot 构建。同时支持完整的 AlohaMini 机器人（双臂 + 移动底盘 + 升降机构）和 AM-ARM200 机械臂。

> 还没完成硬件组装？从这里开始：[AlohaMini](https://github.com/liyiteng/alohamini) · [AM-ARM200](https://github.com/liyiteng/AM-ARM)

## 更新
- **[2025-07-11]** 合并上游 LeRobot v0.6

## 文档

先完成环境配置，然后按照你所使用硬件的工作流程操作。当你需要确切的参数、命令或底层调试工具时，请查阅参考页面。

### 推荐路径

1. [安装](docs/alohamini/install.md) — 准备环境、串口权限以及 Hugging Face 登录。
2. 选择你的机器人工作流程：
   - [AM-ARM200](docs/alohamini/am-arm200.md) — 单台 PC 上的单臂工作流程：校准、遥操作、数据集录制、训练和评估。
   - [AlohaMini 1 / 2 / 2 Pro](docs/alohamini/alohamini.md) — Raspberry Pi + PC 的双臂工作流程：校准、遥操作、数据集录制、训练和评估。

### 参考资料

| 参考资料 | 用途 |
|-----------|------------|
| [硬件配置档案](docs/alohamini/profiles.md) | `--arm_profile` 和 `--robot_model` 参数的含义 |
| [命令速查表](docs/alohamini/commands.md) | 用于环境配置、主机、遥操作、录制、训练、评估以及常见检查的可复制粘贴命令 |
| [调试工具](examples/debug/README.md) | 底层电机、车轮、升降轴、舵机 ID、相位、中点、力矩以及脚本化动作调试功能 |

---

## 团队与联系方式

AlohaMini 由 **Li Yiteng** 和 **Wu Zhiyong** 创建。

- 邮箱：liyiteng+github@gmail.com
- 微信：liyiteng

## 致谢

- [LeRobot](https://github.com/huggingface/lerobot) — 本仓库所依托的软件栈
- [ALOHA](https://tonyzhaozh.github.io/aloha/) — 双臂遥操作范式
- [SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100) — 开创了低成本开源机械臂设计模式
