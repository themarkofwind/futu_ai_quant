#!/usr/bin/env bash
# 常驻服务：持仓分析 / 自选 / 日内做 T
#
# 用法：
#   ./scripts/services.sh <start|stop|restart|status|logs> [服务...]
#
# 服务名（可多选，空格或逗号分隔；默认 all）：
#   analyze | holdings | main     持仓分析
#   watchlist                     自选三槽（是否 AI 见 .env WATCHLIST_USE_AI）
#   intraday | pair              → intraday（日内做 T）
#   all                           analyze + watchlist + intraday
#
# 日志按 ISO 周切分：data/logs/{service}_{YYYY}-W{WW}.log
# 同时维护 {service}.log → 当前周（符号链接）
#
# 示例：
#   ./scripts/services.sh start
#   ./scripts/services.sh restart watchlist intraday
#   python -m futu_ai_quant.utils.weeklog split --retire data/logs/watchlist.log
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/data/logs"
RUN_DIR="$ROOT/data/run"
mkdir -p "$LOG_DIR" "$RUN_DIR"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  PYTHON="$(command -v python)"
fi

# name|module|extra_pgrep
SERVICES=(
  "analyze|futu_ai_quant.cli.analyze|main\\.py"
  "watchlist|futu_ai_quant.cli.watchlist|"
  "intraday|futu_ai_quant.cli.intraday_pair|"
)

usage() {
  cat <<EOF
用法: $(basename "$0") <start|stop|restart|status|logs> [服务...]

动作:
  start     后台启动（已在跑则跳过）
  stop      停止
  restart   先停再启
  status    查看是否在跑
  logs      tail -f 当前周日志（Ctrl+C 退出）

服务（可多选，空格或逗号分隔；省略则为 all）:
  analyze    持仓分析（别名: holdings, main）
  watchlist  自选三槽（AI 开关：.env 中 WATCHLIST_USE_AI）
  intraday   日内做 T（别名: pair；规范名 intraday）
  all        analyze + watchlist + intraday

日志:
  按 ISO 周切分 → data/logs/{service}_YYYY-Www.log
  当前周快捷链接 → data/logs/{service}.log
  拆分旧整文件: $PYTHON -m futu_ai_quant.utils.weeklog --log-dir data/logs split --retire data/logs/watchlist.log

示例:
  $(basename "$0") start
  $(basename "$0") restart watchlist intraday
  $(basename "$0") status watchlist

项目目录: $ROOT
Python:   $PYTHON
EOF
}

canonicalize() {
  local t
  t="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr '-' '_')"
  case "$t" in
    analyze|holdings|main|holding) echo analyze ;;
    watchlist|watch) echo watchlist ;;
    intraday|intradary|intraday_t|pair|t) echo intraday ;;
    all) echo all ;;
    *)
      echo "未知服务: $1（可选 analyze / watchlist / intraday / all）" >&2
      return 1
      ;;
  esac
}

_in_list() {
  local needle="$1"
  shift
  local x
  for x in "$@"; do
    [[ "$x" == "$needle" ]] && return 0
  done
  return 1
}

resolve_targets() {
  local -a raw=()
  local arg part tok canon n
  TARGETS=()

  if [[ $# -eq 0 ]]; then
    raw=(all)
  else
    for arg in "$@"; do
      arg="${arg//,/ }"
      for part in $arg; do
        [[ -z "$part" ]] && continue
        raw+=("$part")
      done
    done
  fi

  for tok in "${raw[@]}"; do
    canon="$(canonicalize "$tok")" || exit 1
    if [[ "$canon" == "all" ]]; then
      for n in analyze watchlist intraday; do
        if ! _in_list "$n" "${TARGETS[@]+"${TARGETS[@]}"}"; then
          TARGETS+=("$n")
        fi
      done
      continue
    fi
    if ! _in_list "$canon" "${TARGETS[@]+"${TARGETS[@]}"}"; then
      TARGETS+=("$canon")
    fi
  done
}

svc_fields() {
  local name="$1"
  local row n module extra
  for row in "${SERVICES[@]}"; do
    IFS='|' read -r n module extra <<EOF
$row
EOF
    if [[ "$n" == "$name" ]]; then
      SVC_NAME="$n"
      SVC_MODULE="$module"
      SVC_EXTRA="$extra"
      SVC_LOG="$("$PYTHON" -m futu_ai_quant.utils.weeklog --log-dir "$LOG_DIR" path "$SVC_NAME")"
      SVC_LOG_LINK="$LOG_DIR/${SVC_NAME}.log"
      SVC_PIDFILE="$RUN_DIR/${n}.pid"
      return 0
    fi
  done
  echo "未知服务: $name" >&2
  return 1
}

pids_for() {
  local module="$1"
  local extra="${2:-}"
  local out=""
  local p
  while IFS= read -r p; do
    [[ -n "$p" ]] || continue
    case " $out " in
      *" $p "*) ;;
      *) out="${out:+$out }$p" ;;
    esac
  done < <(pgrep -f "[p]ython.*-m ${module}( |$)" 2>/dev/null || true)
  if [[ -n "$extra" ]]; then
    while IFS= read -r p; do
      [[ -n "$p" ]] || continue
      case " $out " in
        *" $p "*) ;;
        *) out="${out:+$out }$p" ;;
      esac
    done < <(pgrep -f "[p]ython.*${extra}" 2>/dev/null || true)
  fi
  if [[ -n "$out" ]]; then
    # shellcheck disable=SC2086
    printf '%s\n' $out
  fi
}

is_running() {
  local module="$1"
  local extra="${2:-}"
  local pids
  pids="$(pids_for "$module" "$extra")"
  [[ -n "${pids// }" ]]
}

start_one() {
  local name="$1"
  svc_fields "$name"
  if is_running "$SVC_MODULE" "$SVC_EXTRA"; then
    local pids
    pids="$(pids_for "$SVC_MODULE" "$SVC_EXTRA" | tr '\n' ' ')"
    echo "[skip] $SVC_NAME 已在运行 (pid: $pids)"
    return 0
  fi
  rm -f "$SVC_PIDFILE"
  # weeklog run：托管子进程，跨周自动切换；{service}.log → 当前周
  nohup env PYTHONUNBUFFERED=1 "$PYTHON" -u -m futu_ai_quant.utils.weeklog \
    --log-dir "$LOG_DIR" run "$SVC_NAME" \
    -- "$PYTHON" -u -m "$SVC_MODULE" \
    >/dev/null 2>&1 &
  local wrapper_pid=$!
  echo "$wrapper_pid" >"$SVC_PIDFILE"
  sleep 0.5
  local child_pids
  child_pids="$(pids_for "$SVC_MODULE" "$SVC_EXTRA" | tr '\n' ' ')"
  if [[ -n "${child_pids// }" ]] || kill -0 "$wrapper_pid" 2>/dev/null; then
    echo "[ok]   $SVC_NAME 已启动 (pid: ${child_pids:-$wrapper_pid}) → $SVC_LOG"
    echo "       链接: $SVC_LOG_LINK"
  else
    echo "[fail] $SVC_NAME 启动失败，请查看 $SVC_LOG" >&2
    rm -f "$SVC_PIDFILE"
    return 1
  fi
}

stop_one() {
  local name="$1"
  svc_fields "$name"
  local pids
  pids="$(pids_for "$SVC_MODULE" "$SVC_EXTRA")"
  local wrap_pids=""
  wrap_pids="$(pgrep -f "[p]ython.*-m futu_ai_quant.utils.weeklog .*run ${SVC_NAME}( |$)" 2>/dev/null || true)"
  if [[ -z "${pids// }" && -z "${wrap_pids// }" ]]; then
    echo "[skip] $SVC_NAME 未在运行"
    rm -f "$SVC_PIDFILE"
    return 0
  fi
  # shellcheck disable=SC2086
  [[ -n "${pids}" ]] && kill $pids 2>/dev/null || true
  # shellcheck disable=SC2086
  [[ -n "${wrap_pids}" ]] && kill $wrap_pids 2>/dev/null || true
  local i
  for i in 1 2 3 4 5; do
    if ! is_running "$SVC_MODULE" "$SVC_EXTRA"; then
      break
    fi
    sleep 1
  done
  if is_running "$SVC_MODULE" "$SVC_EXTRA"; then
    pids="$(pids_for "$SVC_MODULE" "$SVC_EXTRA")"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
  wrap_pids="$(pgrep -f "[p]ython.*-m futu_ai_quant.utils.weeklog .*run ${SVC_NAME}( |$)" 2>/dev/null || true)"
  # shellcheck disable=SC2086
  [[ -n "${wrap_pids}" ]] && kill -9 $wrap_pids 2>/dev/null || true
  rm -f "$SVC_PIDFILE"
  echo "[ok]   $SVC_NAME 已停止"
}

status_one() {
  local name="$1"
  svc_fields "$name"
  local pids
  pids="$(pids_for "$SVC_MODULE" "$SVC_EXTRA")"
  if [[ -n "${pids// }" ]]; then
    echo "[up]   $SVC_NAME  pid=$(echo "$pids" | tr '\n' ' ')  log=$SVC_LOG"
  else
    echo "[down] $SVC_NAME"
  fi
}

cmd_logs() {
  local files=()
  local name
  for name in "${TARGETS[@]}"; do
    svc_fields "$name"
    touch "$SVC_LOG"
    files+=("$SVC_LOG")
  done
  echo "跟随日志: ${files[*]}  (Ctrl+C 退出)"
  tail -n 50 -f "${files[@]}"
}

main() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 1
  fi
  local action="$1"
  shift

  case "$action" in
    -h|--help|help)
      usage
      return 0
      ;;
  esac

  resolve_targets "$@"

  case "$action" in
    start)
      local name
      for name in "${TARGETS[@]}"; do
        start_one "$name"
      done
      ;;
    stop)
      local name
      for name in "${TARGETS[@]}"; do
        stop_one "$name"
      done
      ;;
    restart)
      local name
      for name in "${TARGETS[@]}"; do
        stop_one "$name"
      done
      for name in "${TARGETS[@]}"; do
        start_one "$name"
      done
      ;;
    status)
      echo "Python: $PYTHON"
      local name
      for name in "${TARGETS[@]}"; do
        status_one "$name"
      done
      ;;
    logs)
      cmd_logs
      ;;
    *)
      echo "未知动作: $action" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
