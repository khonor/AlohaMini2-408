#!/usr/bin/env python3
"""升降轴（lift_axis, 电机 ID 11）键盘遥控 —— SSH 专用版，不需要 X / pynput / VNC。

## 为什么不用 `examples/debug/axis.py`

`axis.py` 用 `pynput` 抓全局键盘，只能走 X11；而且它硬编码 `--port /dev/ttyACM0`，
在本机上 `/dev/ttyACM*` 编号会漂移。本脚本复用 lerobot 自带的
`TerminalKeyListener`（cbreak 模式直接读控制终端），并用**按键自动重复**模拟「按住」，
与 `02_wheels_ssh.py` 同一套机制，双窗口参数含义也一致。

## 升降轴特有的坑（本脚本已处理）

1. **`LiftAxis.home()` 结束时会把扭矩关掉**（`Torque_Enable=0`），升降轴会失去支撑。
   所以归零之后必须**重新使能扭矩**，否则后面的速度指令完全不动。
2. **高度需要一个零点 `z0`**。没归零时 `get_height_mm()` 只是个「相对本次连接起点的
   位移」，而 `apply_action()` 里的 `descent_floor_mm=5.0` 下限保护会**立刻拦住下降**
   （因为起点恰好就是 0mm）。本脚本在 `--no-home` 时会把软限位临时放开，改用
   「相对行程预算 + 过流 + 堵转」三重保护。
3. **压到底 = 硬堵转**。电机过载保护会让它短暂不响应（读位置抛异常），
   所以每次读都做了容错，连续失败会暂停动作而不是崩掉。
4. **升降轴掉线不安全**：与底盘不同，断电/失控的话它会因重力下滑。所以退出时
   会先把速度写 0，再**保持扭矩使能**（相当于刹车）。

## 按键

    W / K / U / ↑   上升
    S / J / ↓       下降
    Space           立即停止（只停运动，不退出）
    H               重新归零（会向下压到硬限位！）
    Q / ESC         退出

## 用法

⚠️ **不要用 `conda run -n lerobot_alohamini python ...`**：它会用管道接管
stdin，`sys.stdin.isatty()` 变成 False，读不到按键（而且会缓冲 stdout，
界面不实时刷新）。改用下面任一方式：

    conda run --no-capture-output -n lerobot_alohamini python tests/yuntao/01_lift_ssh.py
    # 或者
    conda activate lerobot_alohamini && python tests/yuntao/01_lift_ssh.py

如果没有加 `--no-capture-output`，脚本会自动退回用 `/dev/tty` 读按键并打印警告，
但要实时看到界面还是得用上面的写法。

    # 1) 先只测键盘映射，不碰硬件
    python tests/yuntao/01_lift_ssh.py --dry-run

    # 2) 实测。默认先归零，然后自动抬到 400 mm 停放（避免长期压在底部）
    python tests/yuntao/01_lift_ssh.py

    # 3) 改停放高度 / 干脆不停放
    python tests/yuntao/01_lift_ssh.py --park-mm 300
    python tests/yuntao/01_lift_ssh.py --park-mm -1

    # 4) 完全不想触底（线缆不允许）：不归零，当前位置即为 0mm 参考，
    #    靠相对行程预算保护。注意此时高度只是相对值。
    python tests/yuntao/01_lift_ssh.py --no-home
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

# lerobot.motors 会连带导入 torch，实测启动要 ~5.4 秒。
print("正在加载 lerobot 依赖（含 torch，约 5-6 秒）…", file=sys.stderr, flush=True)

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.robots.alohamini.lift_axis import LiftAxis, LiftAxisConfig
from lerobot.robots.alohamini.model_specs import ROBOT_SPECS
from lerobot.utils.keyboard_input import TerminalKeyListener

# ------------------------ 常量 ------------------------ #

NAME = "lift_axis"

UP_KEYS = {"w", "k", "u", "up"}
DOWN_KEYS = {"s", "j", "down"}
STOP_KEYS = {"space"}
HOME_KEYS = {"h"}
QUIT_KEYS = {"q", "esc"}
ALL_DIR_KEYS = UP_KEYS | DOWN_KEYS


def open_controlling_tty():
    """返回一个可读的控制终端文件对象；取不到返回 None。

    用途：`conda run -n <env> python ...` 会用**管道**接管 stdin，于是
    `sys.stdin.isatty()` 变成 False，按键读不到。但控制终端 `/dev/tty` 仍然可用，
    直接打开它就能恢复键盘输入。（同时也顺带解决了 stdin 被重定向的情况。）
    """
    try:
        f = open("/dev/tty", "r")  # noqa: SIM115 - 由调用方持有到进程结束
    except OSError:
        return None
    if not f.isatty():
        f.close()
        return None
    return f


class LiftTeleop:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.port: str = args.port
        self.robot_model: str = args.robot_model
        self.speed: int = args.speed
        self.do_home: bool = args.home
        self.dry_run: bool = args.dry_run
        self.loop_hz: float = args.loop_hz
        self.status_s: float = args.status_ms / 1000.0
        self.current_limit: float = args.current_limit_ma
        self.over_samples: int = args.over_samples
        self.stall_samples: int = args.stall_samples
        self.hold_s: float = args.hold_ms / 1000.0
        self.initial_hold_s: float = args.initial_hold_ms / 1000.0
        self.max_travel_mm: float = args.max_travel_mm
        self.min_travel_mm: float = args.min_travel_mm
        self.park_mm: float | None = args.park_mm if args.park_mm is not None and args.park_mm >= 0 else None
        self.park_timeout_s: float = args.park_timeout_s

        # 提前构建（dry-run 也要用，用来算真实会下发的 Goal_Velocity 符号）
        self.cfg = self._build_cfg()

        self._lock = threading.Lock()
        # action -> (最近一次收到的时间, 连续收到次数)
        self._state: dict[str, tuple[float, int]] = {}
        self._quit = False
        self._stop = threading.Event()
        # 置位后忽略方向键，直到所有方向键都松开（避免故障后还被同一个按键反复驱动）
        self._latched = False
        self._request_home = False

        self.bus: FeetechMotorsBus | None = None
        self.lift: LiftAxis | None = None
        self.homed = False

        # 状态快照（主循环写，HUD 读，同一线程，无需加锁）
        self.height_mm = 0.0
        self.travel_mm = 0.0
        self.ticks = 0.0
        self.cur_ma = 0.0
        self.read_fail = 0
        self.last_v = 0
        self.fault: str | None = None

        # 内部计数器
        self._tick0 = 0.0
        self._over = 0
        self._stall = 0
        self._move_ref_ticks = 0.0
        self._move_start = 0.0
        self._mm_per_tick = 0.0

    # ------------------------------------------------------------------ 键盘

    def _on_key(self, key: str) -> None:
        """运行在 TerminalKeyListener 的守护线程上。"""
        if key in QUIT_KEYS:
            self._quit = True
            return

        if key in STOP_KEYS:
            # 立即清零速度。注意**不能**清空 _state：锁存要靠「方向键仍被按住」
            # 这个事实来维持，用户真正松手后才解除。
            self._fault("手动停止（Space）")
            return

        if key in HOME_KEYS:
            if self._latched:
                return
            self._request_home = True
            return

        if key in UP_KEYS:
            action = "up"
        elif key in DOWN_KEYS:
            action = "down"
        else:
            return

        now = time.monotonic()
        with self._lock:
            last, count = self._state.get(action, (0.0, 0))
            window = self.initial_hold_s if count <= 1 else self.hold_s
            self._state[action] = (now, count + 1 if now - last < window else 1)

    def _held(self) -> set[str]:
        now = time.monotonic()
        held: set[str] = set()
        with self._lock:
            for action, (last, count) in self._state.items():
                window = self.initial_hold_s if count <= 1 else self.hold_s
                if now - last < window:
                    held.add(action)
                else:
                    self._state[action] = (last, 0)  # 已松开，重置计数
        return held

    # ------------------------------------------------------------------ 硬件

    def _build_cfg(self) -> LiftAxisConfig:
        """构造 LiftAxis 配置。--no-home 时会在 connect() 里把 mm 软限位放开。

        型号相关的参数从 ROBOT_SPECS 取，别用 LiftAxisConfig 的默认值 ——
        默认是 alohamini1 的 84 mm/rev + sts3215；alohamini2 实际是
        131 mm/rev + sts3095，用错会让高度读数差 1.56 倍。
        """
        specs = ROBOT_SPECS[self.robot_model]
        return LiftAxisConfig(
            enabled=True,
            name=NAME,
            bus="left",
            motor_id=11,
            motor_model=specs["lift_motor"],
            lead_mm_per_rev=specs["lead_mm_per_rev"],
            soft_min_mm=0.0,
            soft_max_mm=self.args.max_height_mm,
            descent_floor_mm=self.args.descent_floor_mm,
            home_down_speed=self.args.home_speed,
            home_stall_current_ma=self.args.home_stall_current_ma,
            v_max=self.args.v_max,
        )

    def connect(self) -> None:
        cfg = self.cfg

        motors = {NAME: Motor(cfg.motor_id, cfg.motor_model, MotorNormMode.DEGREES)}
        self.bus = FeetechMotorsBus(port=self.port, motors=motors)
        self.bus.connect(handshake=False)
        print(f"[OK] 已连接 {self.port} @ {self.bus.get_baudrate()} bps")

        # 与 02_wheels_ssh.py / axis.py 一致：解锁 EEPROM -> 关扭矩改模式 -> 开扭矩
        try:
            self.bus.write("Lock", NAME, 0, normalize=False)
        except Exception:
            pass
        try:
            self.bus.disable_torque(NAME)
        except Exception:
            pass
        self.bus.write("Operating_Mode", NAME, OperatingMode.VELOCITY.value, normalize=False)
        self.bus.enable_torque(NAME)
        print("[OK] 已进入 VELOCITY 模式并开启扭矩")

        if self.dry_run:
            return

        self.lift = LiftAxis(cfg, self.bus, None)
        self.lift.attach()
        self.lift.configure()

        self.ticks = self.lift._extended_ticks
        self._tick0 = self.ticks
        # height_mm = dir_sign * ticks * (360/4096) * (lead/360) = dir_sign * ticks * lead/4096
        self._mm_per_tick = (
            cfg.dir_sign * cfg.lead_mm_per_rev * cfg.output_gear_ratio / self.lift._ticks_per_rev
        )

        if self.do_home:
            self.home()
            # 归零必然会在最底部停一下。线缆长度不够时，可以让它归零后立刻抬到
            # 中间偏上的位置停放，避免长期压在底部。
            if self.park_mm is not None:
                self.park(self.park_mm)
        else:
            # 没有零点参考：把 mm 软限位放开，改由「相对行程预算」保护（见 _travel_guard）。
            cfg.soft_min_mm = -1e9
            cfg.soft_max_mm = 1e9
            cfg.descent_floor_mm = -1e9
            print(
                "[WARN] --no-home：当前所在位置即为参考零点，"
                f"本次会话行程预算 {self.min_travel_mm:+.0f} … {self.max_travel_mm:+.0f} mm"
            )

    def park(self, target_mm: float) -> None:
        """闭环把升降轴移动到目标高度（需要已归零）。"""
        assert self.lift is not None and self.bus is not None
        target = float(target_mm)
        print(f"[PARK] 抬升到 {target:.0f} mm …")
        deadline = time.monotonic() + self.park_timeout_s
        while time.monotonic() < deadline:
            if self._stop.is_set() or self._quit:
                break
            h = self.lift.get_height_mm()
            self.height_mm = h
            self.ticks = self.lift._extended_ticks
            self.travel_mm = (self.ticks - self._tick0) * self._mm_per_tick
            if abs(h - target) <= self.lift.cfg.on_target_mm:
                self.lift.stop()
                print(f"[PARK] 到位：{h:.2f} mm")
                return

            # 与主循环同一套保护：过流就放弃，别硬顶
            try:
                raw = self.bus.read("Present_Current", NAME, normalize=False)
                if isinstance(raw, tuple):
                    raw = raw[0]
                self.cur_ma = float(raw or 0) * 6.5
            except Exception:
                self.cur_ma = 0.0
            if self.cur_ma >= self.current_limit:
                self._over += 1
                if self._over >= self.over_samples:
                    self.lift.stop()
                    self._fault(f"抬升过程中过流 {self.cur_ma:.0f} mA，已停止")
                    return
            else:
                self._over = 0

            self.lift.apply_action({f"{NAME}.height_mm": target}, current_height_mm=h)
            time.sleep(0.05)

        self.lift.stop()
        print(f"[PARK] 超时或中断，停在 {self.lift.get_height_mm():.2f} mm")

    def home(self) -> None:
        assert self.lift is not None and self.bus is not None
        print("\n[!] 归零：升降轴将以 home_down_speed 向下压到硬限位，请确认行程内没有人和物。")
        self.lift.home()

        # 压到底后舵机可能因过载保护短暂不响应（"no status packet"），
        # 此时任何总线写入都会抛异常。这里必须容错重试 —— 否则异常会冒出去
        # 把整个脚本带崩（表现为退出码 1，且连不上/看不到原因）。
        for attempt in range(1, 11):
            try:
                # home() 结束时把扭矩关了（电机自由）——必须重新使能，
                # 否则升降轴没有支撑、后续速度指令也不会动。
                self.bus.enable_torque(NAME)
                self.lift._update_extended_ticks()
                break
            except Exception as e:
                if attempt == 10:
                    self._fault(f"归零后无法恢复与升降轴的通信：{e}")
                    return
                print(f"[home] 舵机尚未恢复，重试 {attempt}/10 …")
                time.sleep(0.5)

        self.ticks = self.lift._extended_ticks
        self._tick0 = self.ticks
        self.homed = True
        self.fault = None
        self._over = 0
        self._stall = 0
        print("[OK] 归零完成，当前位置 = 0.00 mm（已重新使能扭矩）")

    def close(self) -> None:
        if self.bus is None:
            return
        try:
            self.bus.write("Goal_Velocity", NAME, 0, normalize=False)
        except Exception:
            pass
        try:
            # 保持扭矩使能 = 主动刹车，避免升降轴因重力下滑
            self.bus.disconnect(disable_torque=False)
        except Exception:
            pass

    # ------------------------------------------------------------------ 保护

    def _fault(self, reason: str) -> None:
        if self.fault is None:
            print(f"\n[FAULT] {reason}")
        self.fault = reason
        self.last_v = 0
        self._stall = 0
        self._over = 0
        self._latched = True
        try:
            if self.bus is not None:
                self.bus.write("Goal_Velocity", NAME, 0, normalize=False)
        except Exception:
            pass

    def _travel_guard(self, v: float) -> float:
        """没归零时用相对行程预算代替 mm 软限位。"""
        if self.homed:
            return v
        if v > 0 and self.travel_mm >= self.max_travel_mm:
            self._fault(f"已达上限行程 {self.travel_mm:.1f} mm（预算 {self.max_travel_mm:.0f} mm）")
            return 0.0
        if v < 0 and self.travel_mm <= self.min_travel_mm:
            self._fault(f"已达下限行程 {self.travel_mm:.1f} mm（预算 {self.min_travel_mm:.0f} mm）")
            return 0.0
        return v

    def _check_safety(self, v: float, now: float) -> float:
        # 1) 过流：压到底 / 撞到东西时立即停
        if v != 0 and self.cur_ma >= self.current_limit:
            self._over += 1
            if self._over >= self.over_samples:
                self._fault(
                    f"过流 {self.cur_ma:.0f} mA ≥ {self.current_limit:.0f} mA"
                    "（很可能已压到底或卡住）"
                )
                return 0.0
        else:
            self._over = 0

        # 2) 堵转：发了速度但位置一点没变（给 0.3s 起步宽限）
        if v != 0:
            if now - self._move_start > 0.3:
                if abs(self.ticks - self._move_ref_ticks) < 2:
                    self._stall += 1
                else:
                    self._stall = 0
                if self._stall >= self.stall_samples:
                    self._fault("电机没有转动（卡住或已到底）")
                    return 0.0

        # 3) 连续读失败：总线/电机可能已经掉线，先停住
        if self.read_fail >= 10:
            self._fault(f"连续 {self.read_fail} 次读不到电机状态，已停止")
            return 0.0

        return v

    # ------------------------------------------------------------------ 主循环

    def run(self) -> None:
        if not sys.stdin.isatty():
            tty = open_controlling_tty()
            if tty is None:
                print(
                    "[ERROR] 读不到终端，无法接收按键。\n"
                    "        请直接在 SSH 交互式终端里运行，不要重定向输入（< file / | ...）。",
                    file=sys.stderr,
                )
                return
            print(
                "[WARN] stdin 不是终端 —— 最常见的原因是用 `conda run` 启动，\n"
                "       它会用管道接管 stdin（也会缓冲 stdout，导致界面不实时刷新）。\n"
                "       本次已自动改用 /dev/tty 读按键，但强烈建议换成下面任一方式：\n"
                "           conda run --no-capture-output -n lerobot_alohamini \\\n"
                "               python tests/yuntao/01_lift_ssh.py\n"
                "           conda activate lerobot_alohamini && \\\n"
                "               python tests/yuntao/01_lift_ssh.py\n",
                file=sys.stderr,
                flush=True,
            )
            sys.stdin = tty  # TerminalKeyListener 读的就是 sys.stdin

        listener = TerminalKeyListener(self._on_key)
        listener.start()

        home_tag = "先归零" if self.do_home else "不归零"
        park_tag = (
            f"归零后抬到 {self.park_mm:.0f} mm 停放" if (self.do_home and self.park_mm is not None) else "停在原位"
        )
        print(f"\n按键： W/K/U/↑ 上升   S/J/↓ 下降   Space 停止   H 重新归零   Q/ESC 退出")
        print(f"速度： 原始速度 ±{self.speed}（v_max={self.args.v_max}）   模式：{home_tag}")
        print(f"停放： {park_tag}   机型：{self.robot_model}（导程 {self.cfg.lead_mm_per_rev:.0f} mm/rev）")
        print(
            f"松开判定： 首次按下后 {self.initial_hold_s * 1000:.0f} ms / 持续重复后 "
            f"{self.hold_s * 1000:.0f} ms"
        )
        print(f"过流保护： ≥{self.current_limit:.0f} mA 连续 {self.over_samples} 次采样\n")

        period = 1.0 / self.loop_hz
        next_status = 0.0
        next_hud = 0.0
        self._move_start = time.monotonic()

        try:
            while not self._quit and not self._stop.is_set():
                now = time.monotonic()

                # ---- 1) 状态采样（限速，别把 1Mbps 总线喂满）----
                if now >= next_status:
                    next_status = now + self.status_s
                    self._sample_status()

                # ---- 2) 归零请求 ----
                if getattr(self, "_request_home", False):
                    self._request_home = False
                    if not self.dry_run and self.lift is not None:
                        self.last_v = 0
                        self.home()

                # ---- 3) 算目标速度 ----
                held = self._held()
                if self._latched:
                    # 故障/手动停止后锁住，必须等方向键全部松开才解除
                    if not (held & ALL_DIR_KEYS):
                        self._latched = False
                        self.fault = None
                    v = 0.0
                elif "up" in held and "down" not in held:
                    v = float(self.speed)
                elif "down" in held and "up" not in held:
                    v = -float(self.speed)
                else:
                    v = 0.0

                # ---- 4) 保护（dry-run 下全部跳过，否则模拟出来的「不动」会被误判为堵转）----
                if v != 0 and not self.dry_run:
                    v = self._check_safety(v, now)
                    v = self._travel_guard(v)

                # ---- 5) 只在速度变化时下发电机（速度模式下的 Goal_Velocity 会保持）----
                v_int = int(v)
                if v_int != self.last_v:
                    if v_int != 0:
                        self._move_ref_ticks = self.ticks
                        self._move_start = now
                        self._stall = 0
                    if not self.dry_run and self.bus is not None:
                        if self.lift is not None:
                            self.lift.apply_action({f"{NAME}.vel": v_int})
                        else:
                            self.bus.write("Goal_Velocity", NAME, v_int, normalize=False)
                    self.last_v = v_int

                # ---- 6) HUD ----
                if now >= next_hud:
                    next_hud = now + 0.15
                    self._draw_hud(held, v_int)

                time.sleep(period)
        except KeyboardInterrupt:
            print("\n[中断]")
        finally:
            print()
            listener.stop()
            # 停住并保持扭矩（升降轴不能自由下落）
            if not self.dry_run and self.bus is not None:
                try:
                    self.bus.write("Goal_Velocity", NAME, 0, normalize=False)
                    self.bus.enable_torque(NAME)
                except Exception:
                    pass
            print("[OK] 速度已归零，扭矩保持使能")

    def _sample_status(self) -> None:
        if self.dry_run:
            return
        assert self.bus is not None and self.lift is not None
        try:
            self.lift._update_extended_ticks()
            self.ticks = self.lift._extended_ticks
            self.height_mm = self.lift.get_height_mm()
            self.travel_mm = (self.ticks - self._tick0) * self._mm_per_tick
            self.read_fail = 0
        except Exception:
            self.read_fail += 1
            return
        try:
            raw = self.bus.read("Present_Current", NAME, normalize=False)
            if isinstance(raw, tuple):
                raw = raw[0]
            self.cur_ma = float(raw or 0) * 6.5
        except Exception:
            self.read_fail += 1

    def _draw_hud(self, held: set[str], v: int) -> None:
        if self.dry_run:
            # 硬件相关的量都没意义，只显示按键和解析出的速度
            raw = v * self.cfg.dir_sign
            print(
                f"\r[dry] 按键={(' '.join(sorted(held)) or '-'):18s} "
                f"速度={v:+5d}  下发 Goal_Velocity={raw:+5d}   "
                f"高度={self.height_mm:+7.2f}mm  行程={self.travel_mm:+7.2f}mm",
                end="",
                flush=True,
            )
            return

        if self.fault:
            state = "FAULT"
        elif v != 0:
            state = "UP" if v > 0 else "DOWN"
        else:
            state = "HOLD"
        zero = "" if self.homed else " (未归零/相对值)"
        print(
            f"\r[{state:5s}] 高度={self.height_mm:+8.2f}mm{zero}  "
            f"行程={self.travel_mm:+8.2f}mm  电流={self.cur_ma:7.1f}mA  "
            f"速度={v:+5d}  按键={(' '.join(sorted(held)) or '-'):12s}",
            end="",
            flush=True,
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description="升降轴键盘遥控（SSH 可用，无需 X/pynput）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--port",
        default="/dev/am_arm_follower_left",
        help="升降轴所在的 bus（option B 下升降轴在 left_bus 上）",
    )
    p.add_argument("--speed", type=int, default=600, help="按键时的原始速度大小")
    p.add_argument("--v-max", type=int, default=1300, help="速度上限（超过会被裁剪）")
    p.add_argument(
        "--home",
        dest="home",
        action="store_true",
        default=True,
        help="启动时先归零（向下压到硬限位）",
    )
    p.add_argument("--no-home", dest="home", action="store_false", help="跳过归零，当前点为参考零点")
    p.add_argument("--home-speed", type=int, default=700, help="归零时的下压速度")
    p.add_argument(
        "--home-stall-current-ma",
        type=int,
        default=300,
        help="归零时判定到底的堵转电流阈值",
    )
    p.add_argument("--max-height-mm", type=float, default=600.0, help="归零后的软上限")
    p.add_argument("--descent-floor-mm", type=float, default=5.0, help="归零后的下降硬下限")
    p.add_argument(
        "--park-mm",
        type=float,
        default=400.0,
        help="归零后自动抬到该高度停放（避免长期压在底部）。设为 <0 表示不动",
    )
    p.add_argument("--park-timeout-s", type=float, default=25.0, help="抬升到 park 高度的超时")
    p.add_argument(
        "--robot-model",
        default="alohamini2",
        choices=sorted(ROBOT_SPECS),
        help="决定丝杆导程/升降电机型号，影响高度换算",
    )
    p.add_argument("--max-travel-mm", type=float, default=550.0, help="不归零时的上行行程预算")
    p.add_argument("--min-travel-mm", type=float, default=-5.0, help="不归零时的下行行程预算")
    p.add_argument("--current-limit-ma", type=float, default=900.0, help="过流保护阈值")
    p.add_argument("--over-samples", type=int, default=3, help="过流连续多少次采样后触发")
    p.add_argument("--stall-samples", type=int, default=10, help="堵转连续多少次采样后触发")
    p.add_argument("--loop-hz", type=float, default=50.0, help="主循环频率")
    p.add_argument("--status-ms", type=float, default=100.0, help="状态采样间隔 ms")
    p.add_argument("--hold-ms", type=float, default=200.0, help="持续重复时的松开判定窗口 ms")
    p.add_argument(
        "--initial-hold-ms",
        type=float,
        default=700.0,
        help="首次按下后的宽容窗口 ms，需大于终端自动重复的首次延迟(250~660ms)",
    )
    p.add_argument("--dry-run", action="store_true", help="只测键盘映射，不连接/不驱动电机")
    p.add_argument("--yes", action="store_true", help="跳过开始前的回车确认")
    args = p.parse_args()

    if args.hold_ms < 60:
        p.error("--hold-ms 至少 60，否则自动重复会被误判为松开")
    if args.initial_hold_ms < args.hold_ms:
        p.error("--initial-hold-ms 应 >= --hold-ms")
    if args.speed > args.v_max:
        p.error("--speed 不应大于 --v-max")

    teleop = LiftTeleop(args)

    def on_signal(signum, _frame):
        print(f"\n[信号 {signum}({signal.Signals(signum).name})] 停止中…")
        teleop._stop.set()
        teleop._quit = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, on_signal)
        except Exception:
            pass

    if not args.dry_run:
        print("⚠️  请确认升降轴行程内没有人和物。")
        if args.home:
            print("   本脚本启动时会先向下归零（压到硬限位）。")
        if not args.yes:
            try:
                input("   准备好后按回车开始（Ctrl+C 取消）：")
            except KeyboardInterrupt:
                print("\n已取消。")
                return
        try:
            teleop.connect()
        except Exception as e:
            print(f"\n[ERROR] 连接失败：{e}", file=sys.stderr)
            teleop.close()
            return

    try:
        teleop.run()
    finally:
        teleop.close()
        print("[DONE]")


if __name__ == "__main__":
    main()
