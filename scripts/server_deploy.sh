#!/usr/bin/env bash
# ============================================================================
# InvestRing 单用途生产部署脚本（issue #537 第四批 C/D）
# ============================================================================
# 只被 CD workflow 经 SSH 调用；服务器端仅依赖 bash/docker/compose/flock/
# sha256sum/curl/awk/sed/mktemp，不依赖 Python。所有决策输入（发布包、期望记录、
# 迁移授权指纹）都由调用方显式传入并在锁内重验——本脚本不猜、不回退到默认值。
#
# 目录约定（--base，默认 /opt/investring）：
#   .env                    共享秘密，人工维护；发布只建符号链接，永不复制/回滚
#   certbot/www             共享 ACME webroot，同上
#   incoming/<id>/          workflow scp 落地的发布包（bundle.json/images.env/
#                           manifest.sha256/files/…）
#   releases/<id>/          自包含发布目录（发布包原样 + compose 入口符号链接）
#   current → releases/<id> 原子激活指针
#   state/deploy.lock       flock 串行化
#   state/accepted-releases.log  追加式 TSV：ts kind release sha run attempt
#   state/last-known-good   最近一次探活通过的 release id
#   state/migrate/          DDL 前持久记录（迁移授权审计与人工恢复依据）
#
# 子命令：
#   deploy    --release-id ID --incoming DIR --expect-accepted SPEC
#             自动部署：整包校验 → 拉镜像 → 只读探测 DB 兼容 → 激活 → 三路探活。
#             发现待迁移/未知状态时在任何写库启动之前停止（exit 4），不激活。
#   rollback  --release-id ID --expect-accepted SPEC
#             手动回滚到服务器已保留的发布：同样先验证兼容性才恢复整包；
#             追加 rollback 记录但不改写既有 auto 记录（祖先关系判据不降级）。
#   redeploy  --release-id ID --expect-accepted SPEC   手动重部署已有发布。
#   migrate   --release-id ID --expect-state FP [--incoming DIR] --expect-accepted SPEC
#             显式迁移升级：授权指纹绑定发布包与实际 DB 状态，锁内重验后才执行
#             prepare；DDL 前持久记录状态；失败保留现场，不自动回退镜像、不 downgrade。
#   record    --last-auto   只读输出最近一条 auto 记录 "<sha> <run>.<attempt>" 或 none。
#
# SPEC（--expect-accepted）：调用方排队时看到的最近 auto 记录（run.attempt 或 none）。
# 锁内重读不一致 ⇒ 有更新的任务已介入 ⇒ 本任务是旧任务，拒绝（exit 3）。
#
# 退出码：0 成功；2 harness/发布包损坏/镜像拉取失败（未改动任何东西）；
# 3 并发占用或旧任务；4 DB 不兼容/待迁移/未知状态/授权指纹过期（未写库、未激活）；
# 5 激活/探活失败（deploy/rollback 已尝试恢复上一发布；migrate 后禁止自动回退镜像）；
# 6 迁移 DDL 失败（现场保留，按 docs/runbooks/deploy-rollback.md 人工处置）。
# ============================================================================
set -euo pipefail

BASE=/opt/investring
PROJECT=investring
KEEP_RELEASES=5
PROBE_ATTEMPTS="${PROBE_ATTEMPTS:-60}"
PROBE_INTERVAL="${PROBE_INTERVAL:-2}"
RELEASE_ID_RE='^[0-9a-f]{7}-[0-9]+\.[0-9]+$'
FP_RE='^[0-9a-f]{64}$'

log()  { printf '%s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }
die()  { local code="$1"; shift; printf '[error] %s\n' "$*" >&2; exit "$code"; }

usage() {
    sed -n '2,40p' "$0" >&2
    exit 2
}

# ------------------------------------------------------------ 参数解析
MODE="${1:-}"; shift || true
RELEASE_ID="" INCOMING="" EXPECT_ACCEPTED="" EXPECT_STATE="" LAST_AUTO=0
while [ $# -gt 0 ]; do
    case "$1" in
        --release-id)      RELEASE_ID="${2:?}"; shift 2 ;;
        --incoming)        INCOMING="${2:?}"; shift 2 ;;
        --expect-accepted) EXPECT_ACCEPTED="${2:?}"; shift 2 ;;
        --expect-state)    EXPECT_STATE="${2:?}"; shift 2 ;;
        --base)            BASE="${2:?}"; shift 2 ;;
        --last-auto)       LAST_AUTO=1; shift ;;
        *) die 2 "未知参数: $1" ;;
    esac
done

STATE="$BASE/state"
RELEASES="$BASE/releases"
LOCK="$STATE/deploy.lock"
ACCEPTED_LOG="$STATE/accepted-releases.log"
LKG="$STATE/last-known-good"
BOOTSTRAP_LOG=""

release_dir() { printf '%s/%s' "$RELEASES" "$1"; }

compose() {
    local dir="$1"; shift
    docker compose --project-name "$PROJECT" --project-directory "$dir" \
        --env-file "$dir/images.env" -f "$dir/docker-compose.yml" "$@"
}

# bootstrap 一次性容器：status/check 只读（后端镜像 entrypoint 为纯 exec 透传）。
# 只取 stdout 中最后一行完整 JSON，滤掉 compose 自身的进度输出。
bootstrap_json() {
    local dir="$1"; shift
    printf '\nrelease=%s bootstrap %s\n' "$(basename "$dir")" "$*" >> "$BOOTSTRAP_LOG"
    compose "$dir" run --rm --no-deps -T backend python -m app.bootstrap "$@" \
        2>>"$BOOTSTRAP_LOG" | grep -E '^\{.*\}$' | tail -1 || true
}

json_field() { # 单行 JSON 的字符串字段提取；键带引号定界，避免子串误配
    sed -n 's/.*"'"$1"'": "\([^"]*\)".*/\1/p' <<<"$2" | tail -1
}

last_auto_spec() {
    [ -f "$ACCEPTED_LOG" ] || { printf 'none'; return; }
    awk -F'\t' '$2 == "auto" { spec = $5 "." $6 } END { print (spec == "" ? "none" : spec) }' \
        "$ACCEPTED_LOG"
}

record_last_auto() { # 只读查询：workflow 用它读排队前的记录快照
    [ -f "$ACCEPTED_LOG" ] || { printf 'none\n'; return; }
    awk -F'\t' '$2 == "auto" { spec = $4 " " $5 "." $6 } END { print (spec == "" ? "none" : spec) }' \
        "$ACCEPTED_LOG"
}

acquire_lock() {
    mkdir -p "$STATE"
    exec 9>"$LOCK"
    flock -n 9 || die 3 "另一部署正在进行（$LOCK 被占用）；本任务按旧任务拒绝"
    # 锁内重读：调用方排队时看到的 auto 记录必须仍是最新，否则已有更新任务介入
    local now; now="$(last_auto_spec)"
    [ -n "$EXPECT_ACCEPTED" ] || die 2 "缺少 --expect-accepted（旧任务判据不可省略）"
    [ "$now" = "$EXPECT_ACCEPTED" ] \
        || die 3 "已接受发布记录已变化（期望 $EXPECT_ACCEPTED，实际 $now）；本任务是旧任务，拒绝执行"
    # 原始 stderr 可能含敏感信息；mktemp 创建 0600 文件，不将内容转发到 CI。
    BOOTSTRAP_LOG="$(mktemp "$STATE/bootstrap-$MODE-$RELEASE_ID.XXXXXX.log")" \
        || die 2 "无法创建 bootstrap 诊断日志；停止部署"
}

verify_manifest() {
    local dir="$1"
    [ -f "$dir/manifest.sha256" ] || die 2 "发布包缺少 manifest.sha256: $dir"
    ( cd "$dir" && sha256sum -c manifest.sha256 ) >/dev/null \
        || die 2 "发布包校验失败（产物缺失/损坏）: $dir"
}

validate_release_id() {
    [[ "$RELEASE_ID" =~ $RELEASE_ID_RE ]] \
        || die 2 "release-id 非法: '$RELEASE_ID'（期望 <sha7>-<run>.<attempt>）"
}

# 发布包身份与 release-id 互证（防止拿错包/换包部署）；在 stage 之前执行，
# 错包在落地目录就被拒绝，不污染 releases/
verify_bundle_identity() {
    local dir="$1" sha run attempt expected_id
    [ -f "$dir/bundle.json" ] || die 2 "发布包缺少 bundle.json: $dir"
    sha="$(sed -n 's/^ *"sha": "\([^"]*\)".*/\1/p' "$dir/bundle.json")"
    run="$(sed -n 's/^ *"run_id": \([0-9]*\).*/\1/p' "$dir/bundle.json")"
    attempt="$(sed -n 's/^ *"run_attempt": \([0-9]*\).*/\1/p' "$dir/bundle.json")"
    expected_id="${sha:0:7}-${run}.${attempt}"
    [ "$expected_id" = "$RELEASE_ID" ] \
        || die 2 "发布包身份（$expected_id）与 --release-id（$RELEASE_ID）不一致；拒绝拿错包部署"
}

pre_stage_identity_check() {
    if [ -d "$(release_dir "$RELEASE_ID")" ]; then
        verify_bundle_identity "$(release_dir "$RELEASE_ID")"
    elif [ -n "$INCOMING" ] && [ -d "$INCOMING" ]; then
        verify_bundle_identity "$INCOMING"
    fi
}

# ------------------------------------------------------------ stage
stage_bundle() {
    local dir; dir="$(release_dir "$RELEASE_ID")"
    if [ -d "$dir" ]; then
        verify_manifest "$dir"   # 幂等重试：已 staged 的包必须仍完整
        log "复用已 staged 发布: $dir"
        return
    fi
    [ -n "$INCOMING" ] || die 2 "缺少 --incoming（$dir 不存在，无包可 stage）"
    [ -d "$INCOMING" ] || die 2 "incoming 目录不存在: $INCOMING"
    for f in bundle.json images.env manifest.sha256 files/docker-compose.yml files/nginx/nginx.conf; do
        [ -f "$INCOMING/$f" ] || die 2 "incoming 发布包缺少 $f"
    done
    [ -f "$BASE/.env" ] || die 2 "$BASE/.env 不存在；共享秘密由人工维护，部署不代建"
    verify_manifest "$INCOMING"
    mkdir -p "$dir"
    cp -a "$INCOMING/." "$dir/"
    # compose 入口全部符号链接进发布包内容：任何被挂载的文件都在 manifest 覆盖内
    ln -s files/docker-compose.yml "$dir/docker-compose.yml"
    mkdir -p "$dir/nginx" "$BASE/certbot/www" "$dir/certbot"
    ln -s ../files/nginx/nginx.conf "$dir/nginx/nginx.conf"
    ln -s "$BASE/.env" "$dir/.env"
    ln -s "$BASE/certbot/www" "$dir/certbot/www"
    if [ "$INCOMING" != "$dir" ]; then rm -rf -- "$INCOMING"; fi   # 整包已复制，落地目录不再保留
    log "已 stage 发布: $dir"
}

pull_images() {
    local dir="$1" ref
    for key in BACKEND_IMAGE_REF FRONTEND_IMAGE_REF NGINX_IMAGE_REF; do
        ref="$(sed -n "s/^${key}=//p" "$dir/images.env")"
        [ -n "$ref" ] || die 2 "images.env 缺少 $key"
        docker pull "$ref" >/dev/null || die 2 "镜像拉取失败: $ref（未改动任何运行状态）"
    done
}

# ------------------------------------------------------------ preflight（DB 兼容）
# 兼容性判据 = 用「目标发布自己的镜像」只读探测：state 必须是 ready，且迁移内容
# 指纹与发布包记录一致。缺表、未知 revision、待迁移、探测错误一律不推定安全。
preflight_db() {
    local dir="$1" status state live_fp bundle_fp
    status="$(bootstrap_json "$dir" status)"
    if [ -z "$status" ]; then
        die 4 "DB 状态探测失败（无输出）；未知状态不部署，请人工检查后重试。诊断日志: $BOOTSTRAP_LOG"
    fi
    state="$(json_field state "$status")"
    bundle_fp="$(sed -n 's/^ *"migration_fingerprint": "\([^"]*\)".*/\1/p' "$dir/bundle.json")"
    case "$state" in
        ready) ;;
        migration_required|empty|unversioned|schema_incomplete)
            die 4 "数据库尚需迁移/初始化（state=$state）；自动部署已在任何写库启动前停止。" \
                "下一步：人工核对后用 migrate 子命令显式授权（绑定本发布包与 status 指纹）" ;;
        unknown_revision)
            die 4 "数据库含未知 alembic revision；禁止自动初始化/迁移/回退，按 runbook 人工决策" ;;
        *)
            die 4 "DB 状态未知（state=${state:-<解析失败>}）；不部署。探测输出: $status。诊断日志: $BOOTSTRAP_LOG" ;;
    esac
    live_fp="$(json_field migration_fingerprint "$status")"
    [ "$live_fp" = "$bundle_fp" ] \
        || die 4 "迁移内容指纹不一致（DB=$live_fp 发布包=$bundle_fp）；兼容性不明，不部署"
    log "DB 兼容确认: state=ready, 迁移指纹 ${live_fp:0:12}…"
}

# ------------------------------------------------------------ activate / probe
activate() {
    local target="$1" tmp="$BASE/current.tmp.$$"
    ln -s "releases/$target" "$tmp"
    mv -T "$tmp" "$BASE/current"    # rename 原子替换，current 不会出现悬空窗口
    log "已激活: releases/$target"
}

reload_nginx() { # 沿用 #104/#119 结论：inode 漂移必须 restart，一致才走无中断 reload
    local dir="$1" host_inode ctr_inode
    host_inode=$(stat -c %i "$dir/nginx/nginx.conf")
    ctr_inode=$(compose "$dir" exec -T nginx stat -c %i /etc/nginx/nginx.conf 2>/dev/null || true)
    if [ -z "$ctr_inode" ] || [ "$host_inode" != "$ctr_inode" ]; then
        log "nginx.conf inode 漂移（host=$host_inode container=${ctr_inode:-<不可读>}），restart 重建挂载"
        compose "$dir" restart nginx
    else
        compose "$dir" exec -T nginx nginx -s reload 2>/dev/null || compose "$dir" restart nginx
    fi
}

start_stack() {
    local dir="$1"
    compose "$dir" up -d --remove-orphans || return 1
    reload_nginx "$dir" || return 1
}

probe_one() {
    local name="$1"; shift
    local i
    for ((i = 1; i <= PROBE_ATTEMPTS; i++)); do
        if "$@" >/dev/null 2>&1; then
            log "✅ 探活通过: $name"
            return 0
        fi
        sleep "$PROBE_INTERVAL"
    done
    warn "探活超时: $name"
    return 1
}

probe_three_way() { # 三路：后端回环 /health、nginx 容器内前端连通、nginx HTTPS 入口
    local dir="$1"
    probe_one "backend /health" curl -sf http://127.0.0.1:8000/health || return 1
    probe_one "frontend via nginx" compose "$dir" exec -T nginx wget -qO- http://frontend:7860/ || return 1
    probe_one "nginx HTTPS 入口" curl -skf https://127.0.0.1/health || return 1
}

dump_logs() {
    docker logs --tail 100 investring-backend 2>&1 || true
    docker logs --tail 50 investring-frontend 2>&1 || true
    docker logs --tail 50 investring-nginx 2>&1 || true
}

# ------------------------------------------------------------ 统一失败处理
# DB_CHANGED=1（migrate 已执行 DDL）时禁止自动回退镜像：新 schema 对旧代码的
# 兼容性未知，回退只会制造第二个事故现场。
DB_CHANGED=0
PREV_ID=""
fail_after_activate() {
    local reason="$1" dir; dir="$(release_dir "$RELEASE_ID")"
    warn "激活后失败: $reason"
    dump_logs
    touch "$dir/.deploy-failed" 2>/dev/null || true   # 保留现场标记，prune 跳过
    append_record failed          # 先持久化失败记录，再做任何恢复动作
    if [ "$DB_CHANGED" = "1" ]; then
        warn "数据库已执行迁移，禁止自动回退镜像/downgrade；按 docs/runbooks/deploy-rollback.md 人工处置"
        exit 5
    fi
    if [ -z "$PREV_ID" ] || [ "$PREV_ID" = "$RELEASE_ID" ]; then
        warn "无上一发布可恢复；现场保留在 $dir"
        exit 5
    fi
    local prev_dir; prev_dir="$(release_dir "$PREV_ID")"
    # 只有兼容条件成立才恢复整包：上一发布的镜像也必须对当前 DB 探出 ready
    local prev_state
    prev_state="$(json_field state "$(bootstrap_json "$prev_dir" status)")"
    if [ "$prev_state" != "ready" ]; then
        warn "上一发布 $PREV_ID 对当前 DB 不兼容（state=${prev_state:-<探测失败>}）；不自动恢复，现场保留。诊断日志: $BOOTSTRAP_LOG"
        exit 5
    fi
    warn "恢复上一发布: $PREV_ID"
    activate "$PREV_ID"
    if start_stack "$prev_dir" && probe_three_way "$prev_dir"; then
        log "已恢复到 $PREV_ID 并探活通过；失败现场保留在 $dir"
        append_record restored "$PREV_ID"
        { printf '%s\n' "$PREV_ID" > "$LKG.tmp" && mv "$LKG.tmp" "$LKG"; } \
            || warn "LKG 更新失败；不回撤已恢复的部署（请人工补记）"
    else
        warn "恢复 $PREV_ID 也失败；两个现场均保留（$dir 与 $prev_dir），立即人工介入"
        append_record restore-failed "$PREV_ID"
    fi
    exit 5
}

# ------------------------------------------------------------ 记录（追加式，不改写历史）
append_record() { # $1=kind [$2=release-id，缺省为本次 RELEASE_ID]；追加式，不改写历史
    local kind="$1" rid="${2:-$RELEASE_ID}" dir sha run attempt
    dir="$(release_dir "$rid")"
    sha="$(sed -n 's/^ *"sha": "\([^"]*\)".*/\1/p' "$dir/bundle.json")"
    run="$(sed -n 's/^ *"run_id": \([0-9]*\).*/\1/p' "$dir/bundle.json")"
    attempt="$(sed -n 's/^ *"run_attempt": \([0-9]*\).*/\1/p' "$dir/bundle.json")"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$kind" "$rid" "$sha" "$run" "$attempt" \
        >> "$ACCEPTED_LOG"
}

# 成功后的标记与清理：失败不回撤已健康的部署（发布本身已探活通过）
mark_success() {
    local kind="$1"
    {
        append_record "$kind"
        printf '%s\n' "$RELEASE_ID" > "$LKG.tmp" && mv "$LKG.tmp" "$LKG"
    } || warn "写发布记录/LKG 失败；部署已健康，不回撤（请人工补记）"
    prune_releases || warn "清理旧发布失败；部署已健康，不回撤"
    docker image prune -f >/dev/null 2>&1 || warn "镜像清理失败；部署已健康，不回撤"
}

prune_releases() {
    local keep id
    keep="$( { tail -n "$KEEP_RELEASES" "$ACCEPTED_LOG" 2>/dev/null | cut -f3
               cat "$LKG" 2>/dev/null; printf '%s\n' "$RELEASE_ID"; } | sort -u )"
    for id in $(ls -1 "$RELEASES" 2>/dev/null); do
        [ -d "$(release_dir "$id")" ] || continue
        # 注意：不能用 `[ … ] && continue`——set -e 下条件为假会直接终止脚本
        if [ -e "$(release_dir "$id")/.deploy-failed" ]; then continue; fi   # 失败现场不自动清理
        if printf '%s\n' "$keep" | grep -qx "$id"; then continue; fi
        rm -rf -- "$(release_dir "$id")"
        log "已清理旧发布: $id"
    done
}

# ------------------------------------------------------------ 部署主链
read_prev() {
    if [ -L "$BASE/current" ]; then
        PREV_ID="$(basename "$(readlink "$BASE/current")")"
    fi
}

activate_and_probe() { # deploy/rollback/redeploy/migrate 共用的激活后半程
    local dir; dir="$(release_dir "$RELEASE_ID")"
    start_stack "$dir" || fail_after_activate "compose up/reload 失败"
    probe_three_way "$dir" || fail_after_activate "三路探活未通过"
}

cmd_deploy_like() { # $1 = 记录 kind（auto/manual-rollback/manual-redeploy/migrate）
    validate_release_id
    acquire_lock
    pre_stage_identity_check
    stage_bundle
    local dir; dir="$(release_dir "$RELEASE_ID")"
    verify_manifest "$dir"
    pull_images "$dir"
    preflight_db "$dir"
    read_prev
    activate "$RELEASE_ID"
    activate_and_probe
    mark_success "$1"
    log "✅ 部署完成: $RELEASE_ID（kind=$1）"
}

# ------------------------------------------------------------ 子命令分发
case "$MODE" in
    record)
        [ "$LAST_AUTO" = "1" ] || die 2 "record 仅支持 --last-auto（只读）"
        record_last_auto
        ;;
    deploy)
        cmd_deploy_like auto
        ;;
    rollback)
        validate_release_id
        [ -d "$(release_dir "$RELEASE_ID")" ] \
            || die 2 "服务器不存在该发布: $RELEASE_ID（回滚只用服务器保留的整包）"
        INCOMING=""
        cmd_deploy_like manual-rollback
        ;;
    redeploy)
        validate_release_id
        [ -d "$(release_dir "$RELEASE_ID")" ] || die 2 "服务器不存在该发布: $RELEASE_ID"
        INCOMING=""
        cmd_deploy_like manual-redeploy
        ;;
    migrate)
        validate_release_id
        [[ "$EXPECT_STATE" =~ $FP_RE ]] \
            || die 2 "--expect-state 必须是 status 输出的 64 位 fingerprint"
        acquire_lock
        pre_stage_identity_check
        stage_bundle
        local_dir="$(release_dir "$RELEASE_ID")"
        verify_manifest "$local_dir"
        pull_images "$local_dir"
        # 锁内重验授权：实际 DB 状态必须仍等于授权时声明的指纹（prepare 内还会再验一次）
        status_json="$(bootstrap_json "$local_dir" status)"
        [ -n "$status_json" ] || die 4 "DB 状态探测失败；授权指纹无法重验，不执行 DDL。诊断日志: $BOOTSTRAP_LOG"
        live_fp="$(json_field fingerprint "$status_json")"
        live_state="$(json_field state "$status_json")"
        [ "$live_fp" = "$EXPECT_STATE" ] \
            || die 4 "DB 状态已变化（授权 $EXPECT_STATE ≠ 实际 ${live_fp:-<无>}）；请重新取 status 并重新授权。诊断日志: $BOOTSTRAP_LOG"
        case "$live_state" in
            ready) warn "DB 已是 ready；prepare 仍将执行（幂等），确认这是有意的重跑" ;;
            unknown_revision|error)
                die 4 "state=$live_state；未知/错误状态禁止迁移，按 runbook 人工决策。诊断日志: $BOOTSTRAP_LOG" ;;
        esac
        # DDL 开始前持久记录：部分失败时这是人工恢复的唯一可靠起点
        mkdir -p "$STATE/migrate"
        record_file="$STATE/migrate/$(date -u +%Y%m%dT%H%M%SZ)-$RELEASE_ID.json"
        {
            printf '{\n'
            printf '  "release_id": "%s",\n' "$RELEASE_ID"
            printf '  "expect_state": "%s",\n' "$EXPECT_STATE"
            printf '  "status_before": %s\n' "$status_json"
            printf '}\n'
        } > "$record_file"
        log "已持久化 DDL 前状态: $record_file"
        if ! compose "$local_dir" run --rm --no-deps -T backend \
                python -m app.bootstrap prepare --expect-state "$EXPECT_STATE"; then
            DB_CHANGED=1
            warn "prepare 失败；不做自动回滚/downgrade，现场与 DDL 前记录均已保留: $record_file"
            exit 6
        fi
        if ! bootstrap_json "$local_dir" check | grep -q '"state": "ready"'; then
            DB_CHANGED=1
            warn "迁移后 check 未达 ready；不自动回退，人工按 $record_file 处置。诊断日志: $BOOTSTRAP_LOG"
            exit 6
        fi
        DB_CHANGED=1
        read_prev
        activate "$RELEASE_ID"
        activate_and_probe     # 失败走 fail_after_activate：DB_CHANGED=1 ⇒ 不回退镜像
        mark_success migrate
        log "✅ 迁移升级完成: $RELEASE_ID"
        ;;
    *)
        usage
        ;;
esac
