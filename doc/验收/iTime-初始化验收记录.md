# iTime 初始化验收记录

日期：2026-10-02；范围：本地仓库、凭据隔离工具与需求文档。此报告不代表产品功能验收。

## 1. 已完成

| 项目 | 结果与证据 |
| --- | --- |
| 独立Git仓库 | 在iTime根目录初始化，分支main |
| 项目级提交身份 | `ENOCH-lyn <enoch@enoch.host>`；配置来源`.git/config` |
| 项目级Git helper | 清空继承helper，按github.com及配置的仓库路径提供凭据 |
| 凭据隔离工具 | `scripts/github.sh` / `scripts/github_auth.py`；官方设备授权、项目文件原子保存，绕开gh钥匙串登录 |
| 原始PRD保留 | 复制后逐字节一致；SHA-256见下 |
| 参考分析 | 核对Topilot固定快照的文档目录、AI规范、测试结构与阶段计划 |
| 需求分析 | 原文逐节分析；15个主题、190个编号问题 |
| 文档体系 | 协作、UI、运维、需求、架构、设计、计划、测试、验收和Git说明 |
| 认证隔离自动测试 | 7项通过；模拟设备授权和假令牌，无真实GitHub或钥匙串调用 |
| 文档检查 | 16份Markdown内部文件链接有效；190个问题编号无重复；暂存差异无空白错误 |
| 真实GitHub授权 | 用户手工完成设备授权，账号ENOCH-lyn；凭据目录700、文件600 |
| 远端仓库与首次推送 | 创建私有 `ENOCH-lyn/iTime`，首次提交 `8cbb936` 已推送main，分支跟踪origin/main |

原始PRD SHA-256：

```text
13b773c9051cb6d38d3279a6b4bac1732d6117ed0028c156e6b482b57ab21194
```

## 2. 已执行验证

```text
python3 -m unittest discover -s tests -p 'test_*.py' -v
Ran 7 tests
OK

sh -n scripts/github.sh
通过
```

认证测试覆盖：不继承全局Token/Git配置注入、文件600权限与原子保存、仓库路径限制、Git store/erase不访问外部助手、设备授权不启动gh/钥匙串、禁用可能改全局认证的命令、无项目令牌时不回退全局账号。

本次初始化前后用户级 `.gitconfig` 内容SHA-256一致：

```text
c3b7fb36dab91482c99eea1ee4604a8ad865eaee37f5976ba29286e57efc40a5
```

用户级 `.config/git/config`、`.config/gh/hosts.yml` 和 `.config/gh/config.yml` 均未创建；真实登录后再次核验，结果一致。令牌文件未被Git跟踪。

通过GitHub API回读确认：仓库名 `ENOCH-lyn/iTime`、`isPrivate=true`、`isEmpty=false`、默认分支 `main`。私有是初始化采用的保守设置，公开策略与许可证仍待用户决定。

## 3. 待完成或不在当前阶段

- 首轮需求答复，首发范围、平台、技术栈、视觉风格和商业规则。
- 产品代码、产品自动测试、Web/App联调、真机验证、性能测试及部署。

文档中的产品测试用例均为待确认和待执行，未声明产品功能已经完成。
