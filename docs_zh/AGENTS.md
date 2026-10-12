本文件为在本仓库中处理代码的 AI agent 提供指引。

> **面向用户的帮助 → [`AGENT_GUIDE.md`](./AGENT_GUIDE.md)**（SO-101 搭建、录制、策略选择、训练时长、评估 —— 附可直接复制粘贴的命令）。

## 项目概览

LeRobot 是一个基于 PyTorch 的库，面向真实世界机器人，提供数据集、预训练策略，以及用于训练、评估、数据采集和机器人控制的工具。它与 Hugging Face Hub 集成，用于模型/数据集的共享。

## 技术栈

Python 3.12+ · PyTorch · Hugging Face (datasets、Hub、accelerate) · draccus (配置/CLI) · Gymnasium (环境) · uv (包管理)

## 开发环境搭建

```bash
uv sync --locked                            # 基础依赖
uv sync --locked --extra test --extra dev   # 测试 + 开发工具
uv sync --locked --extra all                # 全部内容
git lfs install && git lfs pull             # 测试产物
```

## 常用命令

```bash
uv run pytest tests -svv --maxfail=10                 # 运行所有测试
DEVICE=cuda make test-end-to-end                      # 运行所有 E2E 测试
pre-commit run --all-files                           # 代码检查 + 格式化 (ruff、typos、bandit 等)
```

## 架构（`src/lerobot/`）

- **`scripts/`** —— CLI 入口点（`lerobot-train`、`lerobot-eval`、`lerobot-record` 等），在 `pyproject.toml [project.scripts]` 中映射。
- **`configs/`** —— 由 draccus 解析的 Dataclass 配置。`train.py` 中包含顶层配置 `TrainPipelineConfig`。`policies.py` 中包含基类 `PreTrainedConfig`。多态通过 `draccus.ChoiceRegistry` 与 `@register_subclass("name")` 装饰器实现。
- **`policies/`** —— 每个策略位于各自的子目录中。所有策略都继承自 `pretrained.py` 中的 `PreTrainedPolicy`（`nn.Module` + `HubMixin`）。`factory.py` 中使用延迟导入的工厂。
- **`processor/`** —— 数据转换流水线。`ProcessorStep` 为带注册表的基类。`DataProcessorPipeline` / `PolicyProcessorPipeline` 负责串联各个步骤。
- **`datasets/`** —— `LeRobotDataset`（感知 episode 的采样 + 视频解码）与 `LeRobotDatasetMetadata`。
- **`envs/`** —— `configs.py` 中的 `EnvConfig` 基类，`factory.py` 中的工厂。每个环境子类定义 `gym_kwargs` 和 `create_envs()`。
- **`robots/`、`motors/`、`cameras/`、`teleoperators/`** —— 硬件抽象层。
- **`types.py`** 和 **`configs/types.py`** —— 核心类型别名与特征类型定义。

## 仓库结构（`src/` 之外）

- **`tests/`** —— 按模块组织的 Pytest 测试套件。fixtures 位于 `tests/fixtures/`，mocks 位于 `tests/mocks/`。硬件测试使用 `tests/utils.py` 中的 skip 装饰器。通过 `Makefile` 运行的 E2E 测试将输出写入 `tests/outputs/`。
- **`.github/workflows/`** —— CI：`quality.yml`（pre-commit）、`fast_tests.yml`（基础依赖，每个 PR）、`full_tests.yml`（全部 extras + E2E + GPU，审批后运行）、`latest_deps_tests.yml`（每日升级 lockfile）、`security.yml`（TruffleHog）、`release.yml`（打 tag 时发布到 PyPI）。
- **`docs/source/`** —— HF 文档（`.mdx` 文件）。包含各策略的 README、硬件指南、教程。通过 `docs-requirements.txt` 与 CI 工作流单独构建。
- **`examples/`** —— 按使用场景组织的终端用户教程与脚本（数据集创建、训练、硬件搭建）。
- **`docker/`** —— 面向用户（`Dockerfile.user`）与 CI（`Dockerfile.internal`）的 Dockerfile。
- **`benchmarks/`** —— 性能基准测试脚本。
- **根目录文件**：`pyproject.toml`（依赖、构建、工具配置的唯一可信来源）、`Makefile`（E2E 测试目标）、`uv.lock`、`CONTRIBUTING.md` 与 `README.md`（通用信息）。

## 注意事项

- **Mypy 采用渐进式策略**：仅对 `lerobot.envs`、`lerobot.configs`、`lerobot.optim`、`lerobot.model`、`lerobot.cameras`、`lerobot.motors`、`lerobot.transport` 启用严格模式。修改这些模块时请补充类型注解。
- **可选依赖**：许多策略、环境和机器人位于 extras 之后（例如 `lerobot[aloha]`）。为可选包新增的导入必须加保护或采用延迟导入。参见 `pyproject.toml [project.optional-dependencies]`。
- **视频解码**：数据集可以将观测数据存储为视频文件。`LeRobotDataset` 负责帧提取，但测试需要安装 ffmpeg。
- **优先使用 `uv run`** 来执行 Python 命令（而不是直接使用 `python` 或 `pip`）。
