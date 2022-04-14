一个用于樱花动漫 m3u8 文件的下载器


# 安装

git clone 源码后，在项目根目录运行如下命令即可安装：

```shell
# 可以：
git clone https://github.com/GalaxySnail/yhdm_proxy
pip install ./yhdm_proxy
# 或者直接：
pip install git+https://github.com/GalaxySnail/yhdm_proxy
```

或者下载源码包安装：

```shell
# 可以：
curl https://github.com/GalaxySnail/yhdm_proxy/archive/refs/heads/master.tar.gz -o yhdm_proxy.tar.gz
pip install yhdm_proxy.tar.gz
# 或者直接：
pip install https://github.com/GalaxySnail/yhdm_proxy/archive/refs/heads/master.tar.gz
```

也可以下载源码后在项目根目录运行 `pip install .` 安装。
或者也可以使用任何兼容 [PEP 517](https://www.python.org/dev/peps/pep-0517/) 的工具安装。

考虑到本程序适用范围较窄，且依赖专有服务的非公开 API，所以本程序暂时不考虑上传到 pypi.org。请 git clone 仓库或者下载源码包安装。


# 使用方法

运行如下命令：

```shell
python -m yhdm_proxy
```

即可观察到控制台输出信息，服务器已经启动。更多命令行参数可以运行 `python -m yhdm_proxy -h` 查看。

接着，访问如下链接下载 m3u8 文件：

```
http://localhost:8787/m3u8/<url>
```

比如，要下载的 m3u8 链接为：

```
https://example.com/foo/bar.m3u8?a=42
```

则替换为：

```
http://localhost:8787/m3u8/https://example.com/foo/bar.m3u8?a=42
```

即可。

通过 m3u8 下载并拼接完整视频，可以使用 ffmpeg：

```shell
ffmpeg -i 'http://localhost:8787/m3u8/<url>' -c copy out.mp4
```

或者可以用 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 和 [aria2](https://aria2.github.io/) 下载：

```shell
yt-dlp --dowloader aria2c 'http://localhost:8787/m3u8/<url>'
```

详细用法请参见 yt-dlp 的文档。


# LICENSE 许可证

GNU AGPL v3.0 or later

![](https://www.gnu.org/graphics/agplv3-with-text-162x68.png)

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program. If not, see <https://www.gnu.org/licenses/agpl-3.0.html>.

本程序是自由软件：你可以再分发它、和/或依照由自由软件基金会发布的 GNU Affero 通用公共许可证修改它，无论是版本 3 许可证，还是（按你的决定）任何以后版本都可以。

发布该程序是希望它能有用，但是并无保障；甚至连可销售和符合某个特定的目的都不保证。请参看 GNU Affero 通用公共许可证，了解详情。

你应该随程序获得一份 GNU Affero 通用公共许可证的复本。如果没有，请看 <https://www.gnu.org/licenses/agpl-3.0.html>。


# 这是如何做到的？

TODO


# TODO

* 观察到实际上没有复用连接，每次都重新建立一次 tcp 连接，效率较低，考虑在任务间共享 httpx.AsyncClient

* 目前日志直接使用 f-string 和 print 打印，考虑切换到 logging + [structlog](https://www.structlog.org/)

* TrioHTTPWrapper 类包含了太多逻辑，考虑将其拆分，使用 ContextVar 在任务生存期内存储资源

* 编写测试