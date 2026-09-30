#!/usr/bin/env python3
"""`wheels_ssh.py` 的键盘链路测试 —— 在伪终端(pty)里模拟按住/松开/退出。

为什么需要它：`wheels_ssh.py` 依赖「终端自动重复」来模拟按住，
这条逻辑没法靠肉眼确认，必须用可控的按键时序来自动验证。

测试内容：
  1. 模拟「按住 W」（每 80 ms 发一个 w，持续 ~0.8 s）-> 速度应变成非零
  2. 停止发送 ~0.5 s（模拟松开）           -> 速度应回到 0
  3. 模拟「按住 D」（右转）                 -> 三轮应同向
  4. 发送 q                                 -> 进程应正常退出

全程用 `--dry-run`，不连接也不驱动电机，可随时反复运行。

用法：
    python tests/yuntao/test_wheels_ssh_keyboard.py
    pytest tests/yuntao/test_wheels_ssh_keyboard.py -sv
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "wheels_ssh.py")

REPEAT_INTERVAL_S = 0.08  # 模拟终端自动重复的间隔

# 松开后需要等多久才能确认速度归零。
# 注意：单次轻点（收不到任何重复字节）时，脚本无法区分「已松开」和「系统首次延迟未到」，
# 因此会保持 --initial-hold-ms（默认 700ms）的运动。这是终端无「松开」事件导致的固有取舍。
RELEASE_WAIT_S = 1.0

STATUS_LINE_RE = re.compile(r"按键=(?P<keys>\S+)\s+left=\s*(?P<left>-?\d+)\s+back=\s*(?P<back>-?\d+)\s+right=\s*(?P<right>-?\d+)")


class PtySession:
    """把被测脚本跑在 pty 里，可精确控制送键时序并抓取输出。"""

    def __init__(self, argv: list[str]):
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,  # 让脚本自己成为会话首进程，便于整组结束
        )
        os.close(slave)
        self.raw = ""

    def _drain(self, timeout: float = 0.1) -> None:
        while True:
            ready, _, _ = select.select([self.master], [], [], timeout)
            if not ready:
                return
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            if not data:
                return
            self.raw += data.decode(errors="ignore")

    def send(self, keys: str) -> None:
        os.write(self.master, keys.encode())

    def hold(self, key: str, duration_s: float) -> None:
        """模拟「按住」：按自动重复的节奏连续发送同一个键。"""
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            self.send(key)
            self._drain(0.01)
            time.sleep(REPEAT_INTERVAL_S)
        self._drain(0.05)

    def idle(self, duration_s: float) -> None:
        """不发任何键（模拟松开）。"""
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            self._drain(0.05)
            time.sleep(0.02)

    def wait_for(self, needle: str, timeout_s: float = 40.0) -> bool:
        """等待输出中出现某段文字。

        脚本导入 lerobot(含 torch) 需 ~5.4s，所以不能只 sleep 固定时间。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if needle in self.raw.replace("\r", "\n"):
                return True
            self._drain(0.1)
        return False

    def wait_exit(self, timeout_s: float = 5.0) -> int | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self._drain(0.05)
                return self.proc.returncode
            self._drain(0.05)
            time.sleep(0.02)
        return None

    def cleanup(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=3)
        try:
            os.close(self.master)
        except OSError:
            pass

    # ---- 解析 ----
    def statuses(self) -> list[dict[str, int | str]]:
        text = self.raw.replace("\r", "\n")
        out: list[dict[str, int | str]] = []
        for m in STATUS_LINE_RE.finditer(text):
            out.append(
                {
                    "keys": m.group("keys"),
                    "left": int(m.group("left")),
                    "back": int(m.group("back")),
                    "right": int(m.group("right")),
                }
            )
        return out


def test_keyboard_hold_detection() -> None:
    sess = PtySession([sys.executable, SCRIPT, "--dry-run"])
    try:
        # 脚本启动需 ~5.4s（导入 lerobot->torch），等到打印头部提示再开始送键
        assert sess.wait_for("按键：", timeout_s=60), (
            f"脚本未在 60s 内启动完成。已收到的输出:\n{sess.raw[-800:]}"
        )

        # 1) 按住 W -> 应产生非零速度
        sess.hold("w", 0.8)
        during = sess.statuses()
        assert during, f"没有解析到任何状态行。原始输出:\n{sess.raw[-800:]}"
        nonzero = [s for s in during if s["left"] or s["back"] or s["right"]]
        assert nonzero, f"按住 W 期间速度始终为 0。状态行: {during[-5:]}"
        assert "forward" in nonzero[-1]["keys"], f"按键应识别为 forward，实际: {nonzero[-1]}"

        # 2) 松开 -> 速度应回到 0（需等待 initial 窗口过期）
        sess.idle(RELEASE_WAIT_S)
        after = sess.statuses()
        assert after[-1]["left"] == after[-1]["back"] == after[-1]["right"] == 0, (
            f"松开后速度未归零: {after[-1]}"
        )

        # 3) 按住 D -> 右转，三轮应同向
        sess.hold("d", 0.6)
        turned = [s for s in sess.statuses() if s["keys"] == "rotate_right"]
        assert turned, f"未识别到 rotate_right。全部按键: {[s['keys'] for s in sess.statuses()][-8:]}"
        last = turned[-1]
        vals = [last["left"], last["back"], last["right"]]
        assert all(v != 0 for v in vals), f"右转时三轮不应有 0，实际: {vals}"
        assert len({v > 0 for v in vals}) == 1, f"右转时三轮应同号，实际: {vals}"

        # 4) q -> 退出
        sess.send("q")
        rc = sess.wait_exit(timeout_s=5)
        assert rc == 0, f"按 q 后进程应正常退出，实际 returncode={rc}"
    finally:
        sess.cleanup()


def test_initial_repeat_delay_does_not_stutter() -> None:
    """验证双窗口：模拟系统自动重复的「首次延迟」不应导致中途松开。

    按一次 W 后静默 400 ms（长于 --hold-ms=200，短于 --initial-hold-ms=700），
    再补一次重复。若只用单一紧凑窗口，中间速度会掉到 0 造成顿挫。
    """
    sess = PtySession([sys.executable, SCRIPT, "--dry-run"])
    try:
        assert sess.wait_for("按键：", timeout_s=60), f"脚本未启动:\n{sess.raw[-400:]}"

        sess.send("w")
        sess.idle(0.40)  # 长于 hold(200ms)，短于 initial_hold(700ms)

        gap = sess.statuses()
        assert gap, f"期间无状态输出:\n{sess.raw[-400:]}"
        mid = gap[-1]
        assert mid["keys"] == "forward", f"首次延迟期间被误判为松开: {mid}"
        assert mid["left"] != 0 or mid["right"] != 0, f"首次延迟期间速度掉到 0: {mid}"

        # 再补一次重复，随后真正松开
        sess.send("w")
        sess.idle(RELEASE_WAIT_S)
        after = sess.statuses()[-1]
        assert after["left"] == after["back"] == after["right"] == 0, f"松开后未归零: {after}"

        sess.send("q")
        assert sess.wait_exit(timeout_s=5) == 0
    finally:
        sess.cleanup()


if __name__ == "__main__":
    t0 = time.monotonic()
    cases = [test_keyboard_hold_detection, test_initial_repeat_delay_does_not_stutter]
    failed = 0
    for case in cases:
        try:
            case()
            print(f"✅ {case.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"❌ {case.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"\n✅ 全部 {len(cases)} 项通过（耗时 {time.monotonic() - t0:.1f}s）")
