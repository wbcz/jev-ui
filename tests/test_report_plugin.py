"""二开插件的单测：版本校验 + 幂等安装。

**这些测试守的是"静默失效"**——项目里其他 eval 也是同一个理由：
"报告生成了但没插件"这种失败是静默的，报告照常打开、只是没有时间轴，
只有断言能兜住。

安装目标用 `tmp_path` 造一个最小的假报告，不碰真实产物。
"""

import json

import pytest

from jev_ultrafast.framework.report_plugin import install

APP_JS = '<script src="app.js"></script>'


def make_report(tmp_path, *, with_script=False):
    """造一个最小的假报告目录：只要 index.html 在，安装器就认。"""
    report = tmp_path / "allure-report"
    (report / "data").mkdir(parents=True)
    body = f"<html><body>{APP_JS}\n"
    if with_script:
        body += f"    {install.SCRIPT_TAG}\n"
    body += "</body></html>"
    (report / "index.html").write_text(body, encoding="utf-8")
    return report


@pytest.fixture(scope="session")
def ok_versions():
    """本机实际环境的版本，用作"应当通过"的输入。

    **session 级、只探测一次**：`detect_versions()` 要起 allure（Java CLI，1~2 秒）、
    java、ffmpeg 三个子进程；每个测试各探一次会让这套本来 0.3 秒的用例拖到 10 秒。
    需要改动的测试用 `dict(ok_versions)` 拿副本，别改这份共享的。
    """
    return install.detect_versions()


# --------------------------- 版本校验 ---------------------------

def test_versions_file_lists_every_expected_component():
    """版本表要把二开真正依赖的东西列全，否则"校验通过"是假的。

    少列一项 = 那一项升级时插件可能静默失效，而校验说"通过"。
    """
    config = install.load_versions()
    expected = config["expected"]
    for name in ("allure_cli", "allure_pytest", "allure_python_commons", "java",
                 "ffmpeg", "pytest", "python", "browser_harness"):
        assert name in expected, f"版本表里缺 {name}"
    # Backbone / Marionette 在报告页面里，装的时候读不到，只能运行时自查
    assert set(config["runtime_expected"]) == {"backbone", "marionette"}


def test_current_environment_passes(ok_versions):
    """本机当前环境必须通过——否则插件根本装不上，是"红线自检"。"""
    mismatches = install.check_versions(detected=ok_versions)
    assert mismatches == [], f"本机环境与版本表不符：{install.describe_mismatches(mismatches)}"


def test_mismatch_is_detected_exactly(ok_versions):
    """改错一项必须**精确报出来**，而不是笼统说"有错"。"""
    detected = dict(ok_versions)
    detected["allure_cli"] = "2.14.0"
    mismatches = install.check_versions(detected=detected)
    assert len(mismatches) == 1
    assert mismatches[0] == {"name": "allure_cli", "expected": "2.13.8", "detected": "2.14.0"}


def test_prefix_match_tolerates_build_suffix(ok_versions):
    """ffmpeg / java 的版本串带构建后缀，用前缀比。

    实测：ffmpeg 报 `9.0.2-https://www.martin-riedl.de`、java 报 `25.0.4.1+1`。
    要求全等的话，换台机器就装不上了。
    """
    detected = dict(ok_versions)
    detected["ffmpeg"] = "9.0.2-full_build-www.gyan.dev"
    detected["java"] = "25.0.4.1+1"
    assert install.check_versions(detected=detected) == []

    # 但前缀不对时仍要拦住
    detected["ffmpeg"] = "7.0.0-other-build"
    assert [m["name"] for m in install.check_versions(detected=detected)] == ["ffmpeg"]


def test_undetectable_component_is_a_mismatch_not_a_pass(ok_versions):
    """探测不到的项算**不匹配**，不能当"没这回事"。

    把"没探测到"当成通过，就是"校验通过但插件其实装错了"的经典来源。
    """
    detected = dict(ok_versions)
    del detected["java"]
    mismatches = install.check_versions(detected=detected)
    assert [m["name"] for m in mismatches] == ["java"]
    assert mismatches[0]["detected"] == "（探测不到）"


def test_tool_resolution_handles_windows_bat(monkeypatch):
    """`resolve_tool` 要把工具名换成真实路径。

    依据（实测）：Windows 上 `shutil.which("allure")` 找到的是 `allure.BAT`，
    而 `subprocess.run(["allure", ...])` 会 **FileNotFoundError**——
    CreateProcess 不认没有扩展名的 `.bat`。症状看着像"allure 没装"。
    """
    monkeypatch.setattr(install.shutil, "which", lambda tool: rf"D:\tools\{tool}.BAT")
    assert install.resolve_tool("allure") == r"D:\tools\allure.BAT"
    # 找不到时退回原名，让 subprocess 自己去报那个错
    monkeypatch.setattr(install.shutil, "which", lambda tool: None)
    assert install.resolve_tool("allure") == "allure"


# --------------------------- 安装是幂等的 ---------------------------

def test_install_puts_the_plugin_and_the_script_tag(tmp_path, ok_versions):
    report = make_report(tmp_path)
    info = install.install(report, detected=ok_versions)

    assert (report / "plugins" / "step-video" / "index.js").is_file()
    assert (report / "plugins" / "step-video" / "styles.css").is_file()
    assert (report / "plugins" / "step-video" / "versions.json").is_file()
    assert install.SCRIPT_TAG in (report / "index.html").read_text(encoding="utf-8")
    assert info["script 标签"] == "本次新插入"


def test_install_is_idempotent(tmp_path, ok_versions):
    """**报告会反复重新生成再重装**，重复执行不能重复插 `<script>`。

    插两次会让插件加载两遍，用例页上出现两个时间轴区块。
    """
    report = make_report(tmp_path)
    install.install(report, detected=ok_versions)
    install.install(report, detected=ok_versions)
    install.install(report, detected=ok_versions)

    html = (report / "index.html").read_text(encoding="utf-8")
    assert html.count(install.SCRIPT_TAG) == 1, "script 标签被插了多次"
    assert html.count("</body>") == 1, "index.html 被改坏了"


def test_install_after_regeneration_still_works(tmp_path, ok_versions):
    """重新生成报告会冲掉插件，重装一遍要能恢复（脚本标签也被冲掉）。"""
    report = make_report(tmp_path)
    install.install(report, detected=ok_versions)
    # 模拟 allure generate --clean：index.html 回到原始内容，plugins/ 也没了
    report2 = make_report(tmp_path / "again")
    info = install.install(report2, detected=ok_versions)
    assert info["script 标签"] == "本次新插入"
    assert info["版本校验"].startswith("通过")


def test_script_tag_goes_after_app_js(tmp_path, ok_versions):
    """标签必须插在 `</body>` 之前，也就是在 app.js **之后**加载。

    插件要用 `window.allure` 与 `window.Backbone`，它们由 app.js 建立；
    插早了会拿不到，插件静默不工作。
    """
    report = make_report(tmp_path)
    install.install(report, detected=ok_versions)
    html = (report / "index.html").read_text(encoding="utf-8")
    assert html.index(APP_JS) < html.index(install.SCRIPT_TAG)


def test_refuses_to_install_on_mismatch(tmp_path, ok_versions):
    """版本不匹配必须**拒绝安装并报错**（负向断言）。

    装了不匹配的版本可能静默失效——报告照常打开，只是没有时间轴。
    宁可装不上。
    """
    report = make_report(tmp_path)
    detected = dict(ok_versions)
    detected["allure_pytest"] = "2.14.0"        # 正是会让 2.13.8 CLI 丢全部结果的那个版本

    with pytest.raises(RuntimeError, match="拒绝安装"):
        install.install(report, detected=detected)

    assert not (report / "plugins").exists(), "拒绝了却还是把插件拷进去了"
    assert install.SCRIPT_TAG not in (report / "index.html").read_text(encoding="utf-8")


def test_allow_mismatch_is_explicit_and_marked(tmp_path, ok_versions):
    """放行要显式，而且必须**在返回值里标出来**——不能让"校验没过"消失。"""
    report = make_report(tmp_path)
    detected = dict(ok_versions)
    detected["java"] = "11.0.1"

    info = install.install(report, detected=detected, allow_mismatch=True)
    assert "已放行" in info["版本校验"]
    assert "java" in info["版本校验"]


def test_non_report_directory_fails_loudly(tmp_path, ok_versions):
    with pytest.raises(NotADirectoryError, match="index.html"):
        install.install(tmp_path, detected=ok_versions)


def test_is_installed_reflects_reality(tmp_path, ok_versions):
    report = make_report(tmp_path)
    assert install.is_installed(report) is False
    install.install(report, detected=ok_versions)
    assert install.is_installed(report) is True

    # 插件文件被删掉、只剩标签 → 不算装好（否则会以为一切正常）
    (report / "plugins" / "step-video" / "index.js").unlink()
    assert install.is_installed(report) is False


def test_plugin_assets_are_self_consistent():
    """插件自己 fetch 的两个文件必须在同目录里，否则页面 404、插件半死。"""
    plugin_dir = install.PLUGIN_DIR
    for name in ("index.js", "styles.css", "versions.json"):
        assert (plugin_dir / name).is_file(), f"插件缺 {name}"

    source = (plugin_dir / "index.js").read_text(encoding="utf-8")
    # 插件按 PLUGIN_DIR 常量拼这两个路径，名字必须对得上
    assert "plugins/step-video/" in source
    assert "versions.json" in source
    assert "styles.css" in source

    config = json.loads((plugin_dir / "versions.json").read_text(encoding="utf-8"))
    assert config["runtime_expected"]["marionette"] == "3.3.1"


def test_plugin_uses_the_page_own_backbone_not_a_bundled_copy():
    """插件必须挂在**页面自带的** Backbone.Marionette 上。

    实测报告页里只有 `window.Backbone`（Backbone 1.3.3 / Marionette 3.3.1），
    **没有**独立的 `window.Marionette`。自己打包一份会版本冲突，可能整个报告白屏。
    """
    source = (install.PLUGIN_DIR / "index.js").read_text(encoding="utf-8")
    assert "Backbone.Marionette.View.extend" in source
    # 反面：不该出现"自己定义 Backbone / 引入第三方副本"的写法
    assert "window.Marionette.View" not in source
    assert "bundled" not in source.lower()