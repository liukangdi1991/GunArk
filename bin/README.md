# bin

二进制发布包输出目录。

执行：

```bash
./scripts/package_release.sh
```

会生成 `bin/trend-radar-<version>.zip`。该 zip 包内包含 PyInstaller 构建出的 `trend-radar` 可执行文件、前端静态资源、默认配置和安装脚本，不依赖 Docker。
