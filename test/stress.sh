#!/usr/bin/env bash
# Opt-in transport stress bench for oat <-> AgentTool. NOT part of `make test`
# (that's the deterministic integration suite); this one characterizes the
# *link* — integrity, back-to-back reliability vs char-delay, and an op soak.
# The same phases run against the emulator's lossless FIFO (a clean baseline)
# and a real serial port (where desyncs actually appear).
#
# Usage:
#   test/stress.sh --serial /dev/ttyUSB1
#   test/stress.sh --serial-in /tmp/p.in --serial-out /tmp/p.out
#
# Env knobs:
#   PHASES=ABC     which phases to run (any subset of A,B,C)
#   BREP=20        phase B trials per char-delay
#   SOAK=30        phase C iterations
#   TIMEOUT=15     per-request read timeout (s); failures cost this, keep modest
#   DELAYS="..."   phase B char-delay-us list (default "1500 1000 750 500 250 100 0")
#   BCMD=mix       phase B command: "mix" (check/list-modules/list-files) or "check"
#   OAT=path       oat binary (default oat/target/release/oat)
#   RESULTS=dir    output dir for <label>.tsv (default: a mktemp dir, path printed)
#
# Phases:
#   A size-sweep integrity   write N bytes, read back, byte-compare (sector boundaries)
#   B back-to-back vs delay  R rapid trials at each --char-delay-us; failure rate + recovery
#   C mixed-op soak          M iterations of write/edit/read/list/delete
#
# Reads the desync story straight off the wire: on a real UART a request frame's
# first (sync) byte is lost when an Oberon.Loop stall outlasts the inter-byte gap;
# the device self-recovers per frame, so oat's retry (see --retries) clears it.
# See REAL-SERIAL.md.

set -u
CONN=("$@")
[ ${#CONN[@]} -gt 0 ] || { sed -n '2,30p' "$0"; exit 2; }

OAT=${OAT:-oat/target/release/oat}
TO=${TIMEOUT:-15}
BREP=${BREP:-20}
SOAK=${SOAK:-30}
PHASES=${PHASES:-ABC}
DELAYS=${DELAYS:-1500 1000 750 500 250 100 0}
BCMD=${BCMD:-mix}
RESULTS=${RESULTS:-$(mktemp -d)}
mkdir -p "$RESULTS"

# Label from the connection: the serial basename, or "fifo".
case "${CONN[*]}" in
  *--serial\ *) LABEL=$(basename "${CONN[1]}");;
  *)            LABEL=fifo;;
esac
TSV="$RESULTS/$LABEL.tsv"; : >"$TSV"
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
has(){ [[ "$PHASES" == *"$1"* ]]; }

oat(){ "$OAT" "${CONN[@]}" --timeout "$TO" "$@"; }
oatd(){ local d=$1; shift; "$OAT" "${CONN[@]}" --timeout "$TO" --char-delay-us "$d" "$@"; }
gen(){ yes ABCDEFGHIJKLMNOP | tr -d '\n' | head -c "$1"; }    # exact N printable bytes
rec(){ printf '%s\t%s\t%s\t%s\n' "$LABEL" "$1" "$2" "$3" >>"$TSV"; }  # phase key result

echo "== stress $LABEL  conn=[${CONN[*]}] =="
echo "device: $(oat check 2>&1 | head -1)"

# ---- Phase A: size-sweep integrity ------------------------------------------
A_ok=0 A_bad=0
if has A; then
echo "-- A: size-sweep integrity --"
for n in 1 100 671 672 673 1023 1024 1025 1695 1696 1697 2048 3392 4096 6144 8192; do
  gen "$n" >"$W/exp"
  if oat write SsT.Txt <"$W/exp" >/dev/null 2>&1 && oat read SsT.Txt >"$W/got" 2>/dev/null; then
    if cmp -s "$W/exp" "$W/got"; then
      A_ok=$((A_ok+1)); rec A "size=$n" ok
    else
      A_bad=$((A_bad+1)); rec A "size=$n" MISMATCH
      echo "   size $n: MISMATCH exp=$n got=$(wc -c<"$W/got") -- CORRUPTION"
    fi
  else
    A_bad=$((A_bad+1)); rec A "size=$n" ERROR
    echo "   size $n: ERROR (timeout/desync, not corruption)"
  fi
done
oat delete SsT.Txt >/dev/null 2>&1
echo "   A: $A_ok ok, $A_bad bad"
fi

# ---- Phase B: back-to-back reliability vs char-delay -------------------------
if has B; then
echo "-- B: back-to-back reliability vs --char-delay-us (R=$BREP) --"
echo "   delay_us  fails/R  first_fail  recovered"
for d in $DELAYS; do
  f=0 first=-
  for i in $(seq 1 "$BREP"); do
    case "$BCMD" in
      check) cmd=(check);;
      *) case $((i % 3)) in 0) cmd=(check);; 1) cmd=(list-modules);; 2) cmd=(list-files);; esac;;
    esac
    if ! oatd "$d" "${cmd[@]}" >/dev/null 2>&1; then f=$((f+1)); [ "$first" = - ] && first=$i; fi
  done
  r=no; for k in 1 2 3; do oatd 1000 check >/dev/null 2>&1 && { r=yes; break; }; done
  printf '   %8s  %5s/%-3s  %-10s  %s\n' "$d" "$f" "$BREP" "$first" "$r"
  rec B "delay=$d" "fails=$f/$BREP recovered=$r"
done
fi

# ---- Phase C: mixed-op soak --------------------------------------------------
C_fail=0
if has C; then
echo "-- C: mixed-op soak (M=$SOAK iterations) --"
for i in $(seq 1 "$SOAK"); do
  bad=
  { gen 300; printf '\nNEEDLE-%s-MARK\n' "$i"; gen 300; } >"$W/c"
  oat write SoT.Txt <"$W/c" >/dev/null 2>&1 || bad=write
  oat edit SoT.Txt "NEEDLE-$i-MARK" "FOUND-$i-DONE" >/dev/null 2>&1 || bad="${bad:+$bad,}edit"
  oat read SoT.Txt >/dev/null 2>&1 || bad="${bad:+$bad,}read"
  oat list-files >/dev/null 2>&1 || bad="${bad:+$bad,}list"
  oat delete SoT.Txt >/dev/null 2>&1 || bad="${bad:+$bad,}delete"
  [ -n "$bad" ] && { C_fail=$((C_fail+1)); rec C "iter=$i" "FAIL:$bad"; echo "   iter $i: FAIL ($bad)"; }
done
oat delete SoT.Txt >/dev/null 2>&1
echo "   C: $((SOAK-C_fail))/$SOAK clean iterations"
fi

sum="SUMMARY $LABEL:"
has A && sum="$sum  A=$A_ok/$((A_ok+A_bad)) integrity"
has B && sum="$sum  B: see table"
has C && sum="$sum  C=$((SOAK-C_fail))/$SOAK soak"
echo "$sum  (TSV: $TSV)"
