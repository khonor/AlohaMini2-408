# 硬件配置档（Profile）参考

主机侧的 `--robot_model` 参数，以及 PC 侧的 `--robot.robot_model` / `--teleop.arm_profile`
参数，用于选择你的硬件型号。

主机侧 `--robot_model` 与 PC 侧 `--robot.robot_model` 必须使用相同的型号值。
`--teleop.arm_profile` 仅用于连接在 PC 上的主手臂（leader arm）。

## AlohaMini 主机侧（`--robot_model`）

| `--robot_model` | 从手臂（follower） | 底盘轮子 | 升降电机 | 丝杠 |
|-----------------|--------------|-------------|------------|------------|
| `alohamini1` | `so-arm-5dof` | STS3215 ×3 | STS3215 | 84 mm/转 |
| `alohamini2` | `am-follower-6dof` | STS3215 ×3 | STS3095 | 131 mm/转 |
| `alohamini2pro` | `am-follower-6dof-hd` | STS3250 ×3 | STS3095 | 131 mm/转 |

## AM-ARM200 手臂配置档（`--teleop.arm_profile`）

| 产品 SKU | 角色 | `--teleop.arm_profile` |
|-------------|------|-----------------|
| AM-ARM200 | 主手臂 Leader（5V） | `am-leader-6dof` |
| AM-ARM200 | 从手臂 Follower（12V） | `am-follower-6dof` |
| AM-ARM200 Pro | 主手臂 Leader（5V） | `am-leader-6dof` |
| AM-ARM200 Pro | 从手臂 Follower（12V HD） | `am-follower-6dof-hd` |
