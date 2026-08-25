#!/bin/sh
set -eu

export XTABLES_LOCKFILE=/tmp/xtables.lock

if [ "${GUI_ALLOW_PRIVATE_NETWORK:-0}" != "1" ]; then
    for resolver in $(awk '$1 == "nameserver" { print $2 }' /etc/resolv.conf); do
        case "$resolver" in
            *:*)
                ip6tables -w -A OUTPUT -d "$resolver" -p udp --dport 53 -j ACCEPT 2>/dev/null || true
                ip6tables -w -A OUTPUT -d "$resolver" -p tcp --dport 53 -j ACCEPT 2>/dev/null || true
                ;;
            *)
                iptables -w -A OUTPUT -d "$resolver" -p udp --dport 53 -j ACCEPT
                iptables -w -A OUTPUT -d "$resolver" -p tcp --dport 53 -j ACCEPT
                ;;
        esac
    done
    for network in \
        0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 \
        169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 \
        224.0.0.0/4 240.0.0.0/4; do
        iptables -w -A OUTPUT -d "$network" -j REJECT
    done
    if [ -e /proc/net/if_inet6 ]; then
        for network in ::/128 ::1/128 fc00::/7 fe80::/10 ff00::/8; do
            ip6tables -w -A OUTPUT -d "$network" -j REJECT 2>/dev/null || true
        done
    fi
fi

exec setpriv \
    --reuid=10001 --regid=10001 --init-groups \
    --inh-caps=-all --ambient-caps=-all --bounding-set=-all \
    --no-new-privs "$@"
