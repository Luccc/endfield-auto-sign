# 终末地自动签到（国服 + 国际服）

适用于**青龙面板**的明日方舟终末地签到脚本。支持国服与国际服，可分别配置代理。

## 主要特性

- ✅ 多账号支持
- ✅ 国服 + 国际服双服支持
- ✅ **分组代理**：国服强制直连，国际服可单独指定代理
- ✅ 错误信息可读：网络问题会直接告诉你原因和怎么修
- ✅ 防封号随机延迟
- ✅ 青龙多平台推送

## 解决的核心问题

如果你遇到过这个报错：

```
[国际服] 账号1 异常：Expecting value: line 1 column 1 (char 0)
```

**这跟你的 token 无关。** 原因是国际服域名 `zonai.skport.com` 的 CDN 对中国大陆出口 IP 返回了404 的网页，而不是 API 的 JSON 响应。脚本用 JSON 解析器去解析 HTML  webpage，于是抛出这条天书报错。

**解决办法：给国际服配一个境外代理。** 详见下方配置。

---

## 一、快速开始

### 步骤 1：青龙面板添加订阅

| 项目 | 值 |
|---|---|
| 名称 | 终末地签到 |
| 地址 | `https://gitee.com/你的用户名/你的仓库名.git` |
| 分支 | `master` |
| 定时 | `0 0 23* * *` |

### 步骤 2：添加环境变量

在青龙面板「环境变量」中添加：

| 用途 | 变量名 | 是否必填 | 示例值 |
|---|---|---|---|
| 国服 token | `SKYLAND_TOKEN` | 二选一 | `token1;token2` |
| 国际服 token | `SKPORT_TOKEN` | 二选一 | `token1;token2` |
| **国际服代理** | `SKPORT_PROXY` | 国际服必填 | `http://192.168.1.100:7890` |
| 国服代理 | `SKYLAND_PROXY` | 否 | 一般留空 |
| 开启推送 | `SKYLAND_NOTIFY` | 否 | `true` |

> 只需要签到某服就只配对应的 token。多个 token 用 `;`、`,` 或换行分隔。

### 步骤 3：运行

定时 `0 30 8 * * *`（每天 8:30），也可以手动点「运行」测试。

---

## 二、国际服必须配代理 ⚠️

**这一步最容易配错，请务必看完。**

### 为什么需要代理

国际服业务接口 `zonai.skport.com` 的 CDN（Tencent EdgeOne）对**中国大陆出口 IP** 返回 404 网页。实测结论：

- 同一个 URL，境外网络返回完整 JSON 签到数据，境内返回 404 网页
- 该域名根路径和全部业务接口都返回 404 网页
- 国服 `zonai.skland.com` 在国内可正常直连，无需代理

### 关键坑点

1. **代理节点必须在境外/海外地区。** 境内节点无效。
2. **Docker 里 `127.0.0.1` 要改成宿主机局域网 IP。** 容器内的 `127.0.0.1` 指向容器自己，不是你的电脑。这是最高频的失败原因。
3. 如果青龙跑在 Docker，还需要给容器加代理参数（见下方）。

### 验证代理是否有效

配置后，在浏览器或容器内访问：

```
https://zonai.skport.com/web/v1/game/endfield/attendance
```

- 返回 `{"code":0,"message":"OK",...}` → 节点可用，脚本能跑通
- 返回 `<title>SKPORT - 404</title>` → **节点仍是境内的，换一个**

### Docker 额外配置

在青龙的容器启动参数（compose 文件 `environment`段）里加：

```yaml
environment:
  - http_proxy=http://172.17.0.1:7890
  - https_proxy=http://172.17.0.1:7890
```

其中 `172.17.0.1` 换成你宿主机的局域网 IP。

---

## 三、获取 Token

### 国服

1. 登录 [森空岛](https://www.skland.com/)
2. 访问 https://web-api.skland.com/account/info/hg
3. 返回的 `data.content` 就是 token

### 国际服

1. 登录 [skport](https://www.skport.com/)
2. 访问 https://web-api.skport.com/cookie_store/account_token
3. 返回的 `data.content` 就是 token

---

## 四、诊断工具

遇到网络问题可运行诊断脚本，一键区分「网络问题」还是「token 问题」：

```bash
python3 diagnose_skport.py
```

输出示例：

```
代理配置：
  国服→ 强制直连
  国际服     → http://192.168.1.100:7890  (来自 SKPORT_PROXY)

【国际服接口】
  [✅ JSON正常] AS授权服务器    HTTP 400
  [❌ 非JSON(网页)] CRED换取     HTTP 404
  ...

【国服接口对照】
  [✅ JSON正常] 国服签到HTTP 200  -> code=0
```

在青龙中运行：把 `diagnose_skport.py` 拉到脚本目录，然后运行该文件。

---

## 五、常见问题

**Q：报错 `Expecting value: line 1 column 1 (char 0)`？**
A：网络问题，不是 token 问题。给国际服配 `SKPORT_PROXY` 境外代理。

**Q：报错 `代理连接失败`？**
A：代理地址连不上。检查代理是否运行、端口是否正确、容器内是否该用局域网 IP。

**Q：国际服报 404 网页错误，但代理配了？**
A：代理节点还在境内，换一个境外节点。

**Q：提示 `未找到青龙面板的 notify.py`？**
A：正常提示，只影响推送功能，不影响签到。青龙一般自带 notify.py。

**Q：会不会封号？**
A：脚本已内置防封号设计：请求随机延迟 2-8 秒、账号间 15-45 秒、签到前 3-10 秒、User-Agent 池轮换。定时建议设在凌晨。

---

## 六、致谢

- 原项目 [sjtt2/endfield_auto_sign](https://github.com/sjtt2/endfield_auto_sign)
- 上游项目 [FancyCabbage/skyland-auto-sign](https://gitee.com/FancyCabbage/skyland-auto-sign)

## 七、免责声明

本脚本仅供学习交流使用，请勿用于商业用途。资源数据来自鹰角网络官方接口，使用者需自行承担相关责任。
