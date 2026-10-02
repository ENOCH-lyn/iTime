# iTime

面向时间规划、任务执行与 AI 复盘的产品。目标形态为完整 Web 用户端、App 用户端、Web 管理后台，以及负责账号、同步和 AI 调用的服务端。

**当前状态：需求澄清与仓库初始化。尚无可运行业务应用；技术栈、上线范围、视觉风格和商业模式待确认。**

## 已确认方向

- 仓库名为 `iTime`，对外品牌名称待确认。
- Web 和 App 都要有完整用户功能及 UI，Web 额外提供管理员后台。
- 支持账号注册登录，用户内容同步到服务器。
- AI 规划由服务端调用，AI 端点与 Key 由 Web 后台配置。
- 参照 Topilot 建立需求、架构、设计、计划、测试和验收文档，通过详细访谈补全原 PRD。
- Git 身份和 GitHub 凭据只在当前项目配置与保存。

## 开始阅读

1. [文档索引](doc/README.md)
2. [原始 PRD](doc/需求/原始PRD-V1.0.md)
3. [逐项分析与缺口](doc/需求/iTime-PRD分析与缺口.md)
4. [需求访谈问题清单](doc/需求/iTime-需求访谈问题清单.md)
5. [需求规格草案](doc/需求/iTime-需求规格说明书.md)
6. [AI 协作规范](AGENTS.md)

## GitHub

在项目目录运行：

```sh
./scripts/github.sh auth login
./scripts/github.sh auth status
```

按设备授权提示在浏览器手工登录。脚本将 GitHub CLI 凭据放在 `.git/github/`，不写系统钥匙串或用户级配置。不要使用裸 `gh` 登录，也不要提交凭据。详见 [Git 与 GitHub](doc/工程/Git与GitHub.md)。

当前本地提交身份为 `ENOCH-lyn <enoch@enoch.host>`，仅写入 `.git/config`。后续可用 `git config --local user.email "新邮箱"` 改变新提交的邮箱；旧提交不会自动改变。

## 文档维护与验证

需求编号关联问题、设计和测试；建议不会自动变为已确认需求。测试方案描述未来应验证的行为，实际执行结果单独记录在验收文档中。

目前可执行的仓库检查为：

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
sh -n scripts/github.sh
git diff --check
```

目前7项自动测试只验证项目凭据隔离工具；不代表任何产品功能已通过测试。

完整技术栈和工具链确定后，再增加真实的安装、开发、构建、测试及部署命令。
