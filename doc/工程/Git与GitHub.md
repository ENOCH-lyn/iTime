# iTime Git 与 GitHub

## 1. 当前本地设置

- 独立Git仓库，初始分支 `main`。
- 提交显示名 `ENOCH-lyn`，邮箱 `enoch@enoch.host`，仅记录在 `.git/config`。
- Git凭据助手先清空继承的helper，再使用本项目 `scripts/github.sh auth git-credential`。
- `credential.useHttpPath=true`；助手仅为本地 `itime.githubRepository` 配置的仓库路径返回凭据。
- 已在用户完成手动授权的 `ENOCH-lyn` 账号下建立 [iTime](https://github.com/ENOCH-lyn/iTime)，当前采用私有可见性，`origin` 为 `https://github.com/ENOCH-lyn/iTime.git`，`main` 跟踪 `origin/main`。是否公开及许可证待用户决定。

## 2. 手动登录

依赖：Git、Python 3标准库、已安装的GitHub CLI。项目根目录执行：

```sh
./scripts/github.sh auth login
```

脚本显示一次性设备码及 `https://github.com/login/device`。用户在浏览器手工登录、输入设备码并查看授权范围；脚本等待授权结果，将令牌写入：

```text
iTime/.git/github/credentials.json
```

目录权限 `700`，文件权限 `600`，原子写入。不会把令牌写入shell命令、文档或普通输出。浏览器自己的登录会话由用户管理；这里隔离的是本项目Git/GitHub访问凭据。

本项目使用GitHub CLI公开OAuth应用标识发起官方设备授权，仅请求 `repo` scope。**本地存储限定在项目，并不代表OAuth令牌在GitHub上仅拥有iTime权限。** 若要求服务端权限也仅限iTime，需要在仓库创建后另选限定仓库的fine-grained PAT或GitHub App方案。

## 3. 为什么没有直接使用 gh auth login

已核对GitHub CLI 2.96.0的公开实现：即使 `--insecure-storage`，`activateUser` 仍会删除/读取系统钥匙串条目。仅设置 `GH_CONFIG_DIR` 不能满足“不影响全局凭据”的要求。

因此 `scripts/github_auth.py` 直接完成设备授权和文件保存；不调用 `gh auth login`、`switch`、`logout`、`refresh` 或 `setup-git`。代码依据：

- [GitHub CLI authflow](https://github.com/cli/cli/blob/v2.96.0/internal/authflow/flow.go)
- [GitHub CLI auth config](https://github.com/cli/cli/blob/v2.96.0/internal/config/config.go)

常规GitHub命令由包装脚本从项目文件加载令牌，仅传入该次CLI子进程环境；清除继承的全局GitHub令牌及Git配置注入变量，并将潜在Git全局写入重定向到项目认证目录。不得在此项目绕过脚本调用裸 `gh` 进行认证。

## 4. 日常命令

```sh
./scripts/github.sh auth status
./scripts/github.sh api user --jq .login
git status
git diff --check
```

普通 `git fetch` / `git push` 使用本项目helper；当前只允许 `ENOCH-lyn/iTime` 路径。通过本地配置更新允许的目标仓库：

```sh
git config --local itime.githubRepository "OWNER/iTime"
```

其中 `OWNER` 需替换为已确认个人或组织。迁移本地目录后应同步更新 `.git/config` 中helper脚本的绝对路径。其他checkout需各自初始化凭据，不能将此文件提交或复制为共享配置。

需要撤销本地登录时仅删除本项目凭据文件；真正撤销OAuth授权由用户在GitHub授权设置处理，可能影响同一OAuth应用的其他会话，不自动代做。

## 5. 提交邮箱可以后改吗

可以。身份字段与GitHub登录凭据不同：前者写入提交元数据，后者用于远端认证。

只修改以后的提交：

```sh
git config --local user.email "新的邮箱"
git config --local user.name "新的显示名"
```

无需重建仓库、不影响代码，新提交采用新身份；已有提交中的邮箱不变。将旧/新邮箱添加到GitHub账号并验证，有助于提交归属。公开仓库的提交元数据会公开邮箱，可选择GitHub提供的noreply邮箱。

若要改最近尚未共享的提交，可以在核实工作区后修改提交身份；若要改全部历史，则需重写提交，SHA及签名等会改变，已推送历史还涉及协作者同步、PR和强制推送。不自动执行历史重写。

`.mailmap` 可以让支持它的工具统一显示作者名/邮箱，但不会删除旧提交里的原始邮箱。

## 6. 检查

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
sh -n scripts/github.sh
git config --show-origin --show-scope --get-regexp '^(user\.|credential\.)'
git diff --check
```

认证测试全部使用假令牌和模拟响应，不访问真实GitHub、钥匙串或产品环境。2026-10-02另行完成用户手工授权、远端创建和首次推送；真实登录后核验文件权限与全局配置不变，详见初始化验收记录。
