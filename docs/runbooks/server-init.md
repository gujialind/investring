# 服务器初始化指导（一次性，通用版）

> 适用场景：新服务器首次准备环境，之后日常发布一律走 CD（`.github/workflows/deploy.yml`）。
> 本文是**通用指导**而非固化脚本：不同部署者的发行版、网络环境、运维偏好不同，
> 请按自身环境取舍调整（issue #223，替代已退役的 `scripts/server-init.sh`）。
> 目标基线：Ubuntu 24.04 LTS + Docker + 非 root 部署用户；数据库用云 RDS，不装本机。

---

## 1. 初始化清单总览

| 步骤 | 内容 | 频次 |
| --- | --- | --- |
| §2 | SSH 安全加固 | 必做 |
| §3 | Swap（小内存机型防 OOM 兜底） | 按需 |
| §4 | Docker + Compose 安装、镜像加速 | 必做 |
| §5 | 部署用户（非 root） | 必做 |
| §6 | 防火墙/安全组 | 必做 |
| §7 | 部署目录与 RDS 白名单 | 必做 |
| §8 | 安全自查清单 | 收尾 |

## 2. SSH 安全加固

- 禁用 root 密码登录，改用密钥；`/etc/ssh/sshd_config` 关键项：
  `PermitRootLogin prohibit-password`、`PasswordAuthentication no`（确认密钥可用后再关密码）。
- **Ubuntu 24.04 坑①**：SSH 服务单元名是 `ssh.service` / `ssh.socket`（socket activation），
  不是旧发行版的 `sshd.service`；`systemctl restart sshd` 会报 unit 不存在。
- **known_hosts 冲突**：重装/换机后复用旧 IP，客户端 `~/.ssh/known_hosts` 里旧指纹会导致
  `REMOTE HOST IDENTIFICATION HAS CHANGED` 拒连；确认后
  `ssh-keygen -R <host>` 清除对应条目再重连。
- 可选：改非标端口、fail2ban、仅放行固定来源 IP（云安全组层面做更简单）。

## 3. Swap（按需）

4G 及以下内存建议配 1-2G swap 兜底：`fallocate -l 2G /swapfile` → `chmod 600` →
`mkswap` → `swapon`，写 `/etc/fstab` 持久化；`vm.swappiness=10` 减少不必要换页。
大内存机型可跳过。

## 4. Docker + Compose

- 发行版自带源或官方 apt 源安装 `docker-ce docker-ce-cli containerd.io docker-compose-plugin`；
  国内服务器可换云厂商镜像源加速 apt。
- **镜像拉取加速**：`/etc/docker/daemon.json` 配 `registry-mirrors`（各云厂商有专属加速地址，
  以控制台为准），并建议配容器日志轮转（`log-opts: max-size/max-file`）防磁盘打满；改完
  `systemctl restart docker`。
- `systemctl enable --now docker` 开机自启。

## 5. 部署用户（非 root）

- 建专用用户并加 docker 组：`useradd -m -s /bin/bash deploy && usermod -aG docker deploy`。
- **Ubuntu 24.04 坑②**：`useradd -m` 创建的用户默认**账户锁定**（passwd 状态 `L`），
  若该用户需要任何密码相关认证会失败；确认锁定状态 `passwd -S deploy`，必要时
  `passwd -u deploy` 解锁（纯密钥/无登录需求场景可保持锁定）。
- CD 的 SSH 凭据（deploy.yml secrets）对应该用户；业务目录属主设为该用户。

## 6. 防火墙 / 安全组

- 系统层 ufw（如有）：仅放行 22/80/443；**云控制台安全组/防火墙同步放行**——
  只配一侧是常见「连不上」根因。
- 仅开放业务必需端口；数据库端口不对公网开放（走内网/VPC）。

## 7. 部署目录与 RDS

- 建部署目录（如 `/opt/investring`，含 `nginx/`），属主为部署用户；
  `docker-compose.yml` / `nginx.conf` / `.env` 由部署流程落位，secrets 配置见部署文档。
- RDS 白名单添加服务器内网 IP；应用经内网连接，不做公网直连。

## 8. 安全自查清单

- [ ] root 禁止密码登录；部署走非 root 用户
- [ ] SSH 服务名/状态按 24.04 口径确认（`systemctl status ssh`）
- [ ] 安全组与系统防火墙仅放行必需端口，数据库不对公网开放
- [ ] Docker 日志轮转已配置；磁盘余量充足
- [ ] 自动安全更新已开启（`unattended-upgrades`）
- [ ] 备份策略确认（RDS 自动备份 + 关键配置留存）

---

## 附：仓库内脚本定位（避免误用）

| 脚本 | 定位 |
| --- | --- |
| `scripts/verify-frontend.sh` | 前端本地门禁（lint + tsc + build，与 CI 同口径），推送前高频使用 |
| `scripts/visual-verify.sh` | 前端目检脚手架（起服务 + 截图，产物是人看的图，不做通过与否判定）；用法见 `frontend/AGENTS.md` §4「目检」，勿与上一行的门禁混用 |
| `ir-cli/scripts/gen_response_fields.py` | ir-cli 响应字段契约生成器（CI 一致性校验），后端 openapi 变更后运行 |

服务器初始化本身**不再有脚本**——按本文档手工执行即可。
