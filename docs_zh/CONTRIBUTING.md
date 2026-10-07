# 如何为 🤗 LeRobot 做贡献

欢迎所有人参与贡献，我们珍视每个人的付出。代码并不是帮助社区的唯一方式。回答问题、帮助他人、主动联系以及改进文档，都具有巨大的价值。

无论你选择以何种方式贡献，都请务必尊重我们的[行为准则](https://github.com/huggingface/lerobot/blob/main/CODE_OF_CONDUCT.md)和我们的 [AI 政策](https://github.com/huggingface/lerobot/blob/main/AI_POLICY.md)。

## 贡献方式

你可以通过多种方式做出贡献：

- **修复问题：** 解决 bug 或改进现有代码。
- **新功能：** 开发新功能。
- **扩展：** 实现新的模型/策略、机器人或仿真环境，并将数据集上传到 Hugging Face Hub。
- **文档：** 改进示例、指南和文档字符串。
- **反馈：** 提交与 bug 或期望的新功能相关的工单。

如果你不确定从哪里开始，欢迎加入我们的 [Discord 频道](https://discord.gg/q8Dzzpym3f)。

## 开发环境搭建

若要贡献代码，你需要搭建一个开发环境。

### 1. Fork 并克隆

在 GitHub 上 fork 该仓库，然后克隆你的 fork：

```bash
git clone https://github.com/<your-handle>/lerobot.git
cd lerobot
git remote add upstream https://github.com/huggingface/lerobot.git
```

### 2. 环境安装

请按照我们的[安装指南](https://huggingface.co/docs/lerobot/installation)进行环境配置以及从源码安装。

## 运行测试与质量检查

### 代码风格（Pre-commit）

安装 `pre-commit` 钩子，以便在提交前自动运行检查：

```bash
pre-commit install
```

若要对所有文件手动运行检查：

```bash
pre-commit run --all-files
```

### 运行测试

我们使用 `pytest`。首先，通过安装 **git-lfs** 确保你拥有测试所需的产物文件：

```bash
git lfs install
git lfs pull
```

运行完整测试套件（这可能需要安装可选的额外依赖）：

```bash
pytest -sv ./tests
```

或者在开发过程中运行某个特定的测试文件：

```bash
pytest -sv tests/test_specific_feature.py
```

## 提交 Issue 与 Pull Request

请使用模板来填写必填字段和示例。

- **Issue：** 遵循[工单模板](https://github.com/huggingface/lerobot/blob/main/.github/ISSUE_TEMPLATE/bug-report.yml)。
- **Pull request：** 基于 `upstream/main` 进行 rebase，使用具有描述性的分支（不要在 `main` 上工作），在本地运行 `pre-commit` 和测试，并遵循 [PR 模板](https://github.com/huggingface/lerobot/blob/main/.github/PULL_REQUEST_TEMPLATE.md)。

> [!IMPORTANT]
> 社区审查政策：为帮助我们扩展工作规模并营造协作氛围，我们请求贡献者在自己的 PR 获得关注之前，先审查至少一位其他人的开放 PR。这种共同分担能成倍提升我们的审查能力，并帮助所有人的代码更快地被合并！

在提交 PR 并完成同行审查后，LeRobot 团队的一名成员将审查你的贡献。

感谢你为 LeRobot 做出贡献！
