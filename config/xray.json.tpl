{
  "log": {"loglevel": "warning", "error": "/var/log/xray/error.log"},
  "inbounds": [{
    "listen": "0.0.0.0",
    "port": $reality_port,
    "protocol": "vless",
    "settings": {
      "decryption": "none",
      "clients": [{"id": "$uuid", "flow": "xtls-rprx-vision"}]
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "show": false,
        "target": "$sni:443",
        "serverNames": ["$sni"],
        "privateKey": "$private_key",
        "shortIds": ["$short_id"]
      }
    }
  }],
  "outbounds": [
    {"protocol": "freedom", "tag": "internet", "settings": {"domainStrategy": "UseIP"}},
    {"protocol": "blackhole", "tag": "block"}
  ],
  "routing": {
    "domainStrategy": "IPOnDemand",
    "rules": [{
      "type": "field",
      "ip": ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
             "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
             "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4", "::/96",
             "64:ff9b::/96", "64:ff9b:1::/48", "fc00::/7",
             "fe80::/10", "ff00::/8"],
      "outboundTag": "block"
    }]
  }
}
