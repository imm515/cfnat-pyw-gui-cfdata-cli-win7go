# cfnat 本地订阅生成器 GUI

基于 `tkinter` 的图形界面版本，用于监控优选过程、提取有效 IP、生成本地订阅文件，并提供本地 HTTP 订阅地址。

本公开仓库仅保留：

- `cfnat_sub_gui.pyw`
- `README.md`
- `cfdata-cli-win7-experimental.exe`
- `LICENSE`

不包含测试脚本、历史开发记录、私有路径或其它非公开资料。

## 当前版本说明

- `cfdata-cli-win7-experimental.exe` 为 Windows 7 兼容实验版
- 该版本主要用于兼容性验证，不按正式稳定版承诺
- GUI 会优先调用仓库内同目录下的该 EXE

## 主要功能

- 实时监控优选程序输出并提取有效 IP
- 基于模板节点自动生成 `subscription.txt`
- 提供本地 HTTP 订阅地址，默认 `http://127.0.0.1:8888/sub`
- 支持历史订阅恢复，重启后可继续提供订阅
- 支持手动 IP 启动
- 提供“测速当前订阅”能力

## 扫描与订阅逻辑

- 默认延迟阈值为 `300ms`
- 默认优先建议有效 IP 数量最多的 colo
- 订阅 IP 达到稳定刷新次数后再切换，减少频繁波动
- 发现异常切换或 IP 池耗尽时会给出提示

## 当前订阅测速

GUI 中提供“测速当前订阅”按钮，用于对当前订阅 IP 做一次独立测速。

特点：

- 首次运行但已存在历史 `subscription.txt` 时，也可直接测速
- 测速结果保留在日志区，不会被后续 `IP刷新` 覆盖
- 固定使用 `10MB` 下载文件
- 结果显示为 `MB/s`

## 测速原理

测速不会走系统代理或环境变量代理。

实现方式：

- 使用 Python 标准库 `socket + ssl`
- 直接连接当前订阅对应的目标 IP 和端口
- HTTPS 场景下保留目标站点的 `Host/SNI`
- 按时间窗口统计下载速度并输出结果

这更接近“当前订阅 IP 直连测速目标时的可用吞吐”，不等同于完整代理链路带宽。

## 快速开始

前置要求：

- Windows
- Python 3.6+
- 可用的 `tkinter`
- 节点模板文件 `nodes.txt`

启动方式：

```text
cfnat_sub_gui.pyw
```

或：

```bash
python cfnat_sub_gui.pyw
```

## 节点模板

`nodes.txt` 支持常见公开格式，例如：

- `vless://...`
- `vmess://...`

程序会按当前优选结果替换目标节点地址，并生成新的订阅内容。

## 本地产物

运行过程中可能生成或更新：

- `subscription.txt`
- `cfnat_sub.pid`
- `logs/` 下的日志文件

这些都属于本地运行产物，不属于公开仓库必须提交的内容。

## 致谢

### 原始项目

- [CF_NAT](https://t.me/CF_NAT) - 原始项目来源
  - **Telegram 频道**: [@CF_NAT](https://t.me/CF_NAT)

## License

See [LICENSE](LICENSE).
