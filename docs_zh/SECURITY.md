# 安全策略

## 项目状态与理念

迄今为止，`lerobot` 主要是一个研究与原型开发工具，因此部署安全此前一直不是重点。随着 `lerobot` 不断被采用并部署到生产环境中，我们正在更加密切地关注这类问题。

幸运的是，作为一个开源项目，社区也可以通过报告和修复漏洞来提供帮助。我们感谢你负责任地披露所发现的问题，并将尽一切努力认可你的贡献。

## 报告漏洞

如需报告安全问题，请使用 GitHub Security Advisory 的[“Report a Vulnerability”](https://github.com/huggingface/lerobot/security/advisories/new)标签页。

`lerobot` 团队将发送回复，说明处理你的报告所采取的后续步骤。在对你的报告作出初步答复后，安全团队会持续向你通报修复进展和完整公告的情况，并可能要求你提供更多信息或指导。

#### Hugging Face 安全团队

由于本项目属于 Hugging Face 生态系统的一部分，你也可以直接将漏洞报告提交至：**[security@huggingface.co](mailto:security@huggingface.co)**。HF 安全团队的人员将审阅该报告并给出后续步骤建议。

#### 开源披露

如果要报告的是仅与开源代码库相关（而非底层 Hub 基础设施）的漏洞，你也可以使用 [Huntr](https://huntr.com)，这是一个面向开源软件的漏洞披露项目。

## 支持的版本

目前，我们将 `lerobot` 视为滚动发布版本。我们优先为最新的可用版本（`main` 分支）提供安全更新。

| 版本  | 是否支持 |
| -------- | --------- |
| 最新版本   | ✅        |
| < 最新版本 | ❌        |

## 安全使用指南

`lerobot` 与 Hugging Face Hub 紧密耦合，用于共享数据和预训练策略。下载他人上传的产物时，你会面临各种风险。请阅读以下建议，以确保你的运行时环境和机器人环境安全。

### 远程产物（权重与策略）

上传到 Hugging Face Hub 的模型和策略有多种格式。我们强烈建议以 [`safetensors`](https://github.com/huggingface/safetensors) 格式上传和下载模型。

`safetensors` 是专门为防止在你的系统上执行任意代码而开发的，这在物理硬件/机器人上运行软件时至关重要。

为避免从不安全的格式（例如 `pickle`）加载模型，你应确保优先使用 `safetensors` 文件。

### 远程代码

Hub 上的某些模型或环境可能需要 `trust_remote_code=True` 才能运行自定义架构代码。

使用该参数时，请**始终**核实建模文件的内容。我们建议在加载远程代码时指定具体的 `revision`（提交哈希），以确保你免受仓库中未经核实的更新的影响。
