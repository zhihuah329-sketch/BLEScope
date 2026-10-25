# 发布检查

- 使用 `python -m unittest discover -v` 检查协议、ACK、本地滤波和设备选择。
- 使用 `python qt_app.py --self-check` 检查模拟界面。
- 使用 `python build_release.py` 生成程序与便携压缩包。
- 再运行 `dist/BLEScope.exe --self-check`，确认打包程序可启动、绘图并正常退出。
- 源码仓库不提交 `sessions/`、真实日志、构建输出、设备地址或授权信息。
- 发布资产只上传 `release/BLEScope-<version>-Windows-x64.zip`，包含许可声明。
- 真机连接验证与模拟自检分别记录；模拟测试不能代替硬件参数确认。
