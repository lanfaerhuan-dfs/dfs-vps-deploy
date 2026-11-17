# 电风扇-vless-reality-clash
# Mihomo / Clash Verge Rev
# ACL4SSR 规则每天自动拉取更新

mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false

profile:
  store-selected: true
  store-fake-ip: true

dns:
  enable: true

  listen: 127.0.0.1:1053

  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16

  nameserver:
    - 223.5.5.5
    - 119.29.29.29

  fallback:
    - 1.1.1.1
    - 8.8.8.8

  # 节点地址从裸 IP 改成 DuckDNS 后，专门用它解析节点域名。
  # 这样不依赖代理本身先建立连接。
  proxy-server-nameserver:
    - 223.5.5.5
    - 119.29.29.29

  fallback-filter:
    geoip: true
    geoip-code: CN

proxies:
  - name: "DFS-VLESS-REALITY-VISION"
    type: vless

    # 关键：以后不要再写 VPS 裸 IP。
    server: $domain

    # 当前实际端口，迁移时保持不变。
    port: $reality_port

    uuid: "$uuid"
    network: tcp
    tls: true
    udp: true
    flow: xtls-rprx-vision

    # 当前服务端 REALITY 参数；迁移服务器时原样保留。
    servername: $sni
    client-fingerprint: chrome

    reality-opts:
      public-key: "$public_key"
      short-id: "$short_id"

proxy-groups:
  - name: 节点选择
    type: select
    proxies:
      - DFS-VLESS-REALITY-VISION
      - DIRECT

  - name: ♻️ 自动选择
    type: url-test
    proxies:
      - DFS-VLESS-REALITY-VISION
    url: https://www.gstatic.com/generate_204
    interval: 300

  - name: 漏网之鱼
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: 广告拦截
    type: select
    proxies:
      - REJECT
      - DIRECT

  - name: 应用净化
    type: select
    proxies:
      - REJECT
      - DIRECT

  - name: 国内媒体
    type: select
    proxies:
      - DIRECT
      - 节点选择

  - name: 国外媒体
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: 苹果服务
    type: select
    proxies:
      - DIRECT
      - 节点选择
      - ♻️ 自动选择

  - name: Ⓜ️ 微软服务
    type: select
    proxies:
      - DIRECT
      - 节点选择
      - ♻️ 自动选择

  - name: Ⓜ️ 微软Bing
    type: select
    proxies:
      - DIRECT
      - 节点选择
      - ♻️ 自动选择

  - name: Ⓜ️ 微软云盘
    type: select
    proxies:
      - DIRECT
      - 节点选择
      - ♻️ 自动选择

  - name: 电报消息
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: OpenAI/ChatGPT
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: AI平台
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: 谷歌服务
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: 油管视频
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: 奈飞视频
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: GitHub
    type: select
    proxies:
      - 节点选择
      - ♻️ 自动选择
      - DIRECT

  - name: 游戏平台
    type: select
    proxies:
      - DIRECT
      - 节点选择
      - ♻️ 自动选择

  - name: 下载
    type: select
    proxies:
      - DIRECT
      - 节点选择
      - ♻️ 自动选择

rule-providers:
  LocalAreaNetwork:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/LocalAreaNetwork.yaml"
    path: ./rule_provider/LocalAreaNetwork.yaml
    interval: 86400

  UnBan:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/UnBan.yaml"
    path: ./rule_provider/UnBan.yaml
    interval: 86400

  BanAD:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/BanAD.yaml"
    path: ./rule_provider/BanAD.yaml
    interval: 86400

  BanProgramAD:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/BanProgramAD.yaml"
    path: ./rule_provider/BanProgramAD.yaml
    interval: 86400

  ChinaDomain:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/ChinaDomain.yaml"
    path: ./rule_provider/ChinaDomain.yaml
    interval: 86400

  ChinaCompanyIp:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/ChinaCompanyIp.yaml"
    path: ./rule_provider/ChinaCompanyIp.yaml
    interval: 86400

  ChinaIp:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/ChinaIp.yaml"
    path: ./rule_provider/ChinaIp.yaml
    interval: 86400

  ChinaIpV6:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/ChinaIpV6.yaml"
    path: ./rule_provider/ChinaIpV6.yaml
    interval: 86400

  ChinaMedia:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/ChinaMedia.yaml"
    path: ./rule_provider/ChinaMedia.yaml
    interval: 86400

  ProxyMedia:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/ProxyMedia.yaml"
    path: ./rule_provider/ProxyMedia.yaml
    interval: 86400

  ProxyGFWlist:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/ProxyGFWlist.yaml"
    path: ./rule_provider/ProxyGFWlist.yaml
    interval: 86400

  DownloadBase:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Download.yaml"
    path: ./rule_provider/Download.yaml
    interval: 86400

  Apple:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Apple.yaml"
    path: ./rule_provider/Ruleset/Apple.yaml
    interval: 86400

  Microsoft:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Microsoft.yaml"
    path: ./rule_provider/Ruleset/Microsoft.yaml
    interval: 86400

  Bing:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Bing.yaml"
    path: ./rule_provider/Ruleset/Bing.yaml
    interval: 86400

  OneDrive:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/OneDrive.yaml"
    path: ./rule_provider/Ruleset/OneDrive.yaml
    interval: 86400

  Telegram:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Telegram.yaml"
    path: ./rule_provider/Ruleset/Telegram.yaml
    interval: 86400

  OpenAi:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/OpenAi.yaml"
    path: ./rule_provider/Ruleset/OpenAi.yaml
    interval: 86400

  AI:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/AI.yaml"
    path: ./rule_provider/Ruleset/AI.yaml
    interval: 86400

  Claude:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Claude.yaml"
    path: ./rule_provider/Ruleset/Claude.yaml
    interval: 86400

  Gemini:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Gemini.yaml"
    path: ./rule_provider/Ruleset/Gemini.yaml
    interval: 86400

  Google:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Google.yaml"
    path: ./rule_provider/Ruleset/Google.yaml
    interval: 86400

  YouTube:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/YouTube.yaml"
    path: ./rule_provider/Ruleset/YouTube.yaml
    interval: 86400

  Netflix:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Netflix.yaml"
    path: ./rule_provider/Ruleset/Netflix.yaml
    interval: 86400

  Github:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Github.yaml"
    path: ./rule_provider/Ruleset/Github.yaml
    interval: 86400

  Steam:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/Steam.yaml"
    path: ./rule_provider/Ruleset/Steam.yaml
    interval: 86400

  SteamCN:
    type: http
    behavior: classical
    format: yaml
    url: "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Providers/Ruleset/SteamCN.yaml"
    path: ./rule_provider/Ruleset/SteamCN.yaml
    interval: 86400

rules:
  # 局域网 / 明确直连
  - RULE-SET,LocalAreaNetwork,DIRECT
  - RULE-SET,UnBan,DIRECT

  # 广告
  - RULE-SET,BanAD,广告拦截
  - RULE-SET,BanProgramAD,应用净化

  # 服务细分：越具体的放越前
  - RULE-SET,Apple,苹果服务

  - RULE-SET,Bing,Ⓜ️ 微软Bing
  - RULE-SET,OneDrive,Ⓜ️ 微软云盘
  - RULE-SET,Microsoft,Ⓜ️ 微软服务

  - RULE-SET,OpenAi,OpenAI/ChatGPT
  - RULE-SET,Claude,AI平台
  - RULE-SET,Gemini,AI平台
  - RULE-SET,AI,AI平台

  - RULE-SET,Telegram,电报消息
  - RULE-SET,Github,GitHub

  # YouTube 必须在 Google 前面：
  # ACL4SSR 当前 Google.yaml 本身包含 youtube.com / youtu.be / ytimg.com 等。
  - RULE-SET,YouTube,油管视频
  - RULE-SET,Google,谷歌服务

  - RULE-SET,Netflix,奈飞视频

  - RULE-SET,SteamCN,DIRECT
  - RULE-SET,Steam,游戏平台

  # 国内媒体 / 下载
  - RULE-SET,ChinaMedia,国内媒体
  - RULE-SET,DownloadBase,下载

  # 国外大盘必须在 China IP 规则之前，避免先因目标 IP 落在中国网段而被直连。
  - RULE-SET,ProxyMedia,国外媒体
  - RULE-SET,ProxyGFWlist,节点选择

  # 国内大盘
  - RULE-SET,ChinaDomain,DIRECT
  - RULE-SET,ChinaCompanyIp,DIRECT
  - RULE-SET,ChinaIp,DIRECT
  - RULE-SET,ChinaIpV6,DIRECT

  - GEOIP,CN,DIRECT
  - MATCH,漏网之鱼
