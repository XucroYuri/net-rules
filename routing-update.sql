-- Generated from the current local v2rayN database for payment routing.
-- PAYMENT rule is first in V3/V4 whitelist profiles and precedes geosite:cn/direct rules.
DELETE FROM RoutingItem;
INSERT INTO RoutingItem (Id,Remarks,Url,RuleSet,RuleNum,Enabled,Locked,CustomIcon,CustomRulesetPath4Singbox,DomainStrategy,DomainStrategy4Singbox,Sort,IsActive) VALUES ('4988228271039938058','V3-黑名单(Blacklist)','','[
  {
    "Id": "5385894871694662575",
    "OutboundTag": "direct",
    "Protocol": [
      "bittorrent"
    ],
    "Enabled": true,
    "Remarks": "绕过bittorrent"
  },
  {
    "Id": "5101628732454074012",
    "OutboundTag": "proxy",
    "Domain": [
      "api.ip.sb"
    ],
    "Enabled": true,
    "Remarks": "api.ip.sb"
  },
  {
    "Id": "4904555860631299339",
    "OutboundTag": "proxy",
    "Domain": [
      "domain:googleapis.cn",
      "domain:gstatic.com"
    ],
    "Enabled": true,
    "Remarks": "Google cn"
  },
  {
    "Id": "4800581409928580480",
    "Port": "443",
    "Network": "udp",
    "OutboundTag": "block",
    "Enabled": true,
    "Remarks": "阻断udp443"
  },
  {
    "Id": "4675169851728933212",
    "OutboundTag": "direct",
    "Ip": [
      "geoip:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网IP"
  },
  {
    "Id": "5509559977237813666",
    "OutboundTag": "direct",
    "Domain": [
      "geosite:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网域名"
  },
  {
    "Id": "5408473550181291275",
    "OutboundTag": "proxy",
    "Ip": [
      "1.1.1.1",
      "1.0.0.1",
      "2606:4700:4700::1111",
      "2606:4700:4700::1001",
      "1.1.1.2",
      "1.0.0.2",
      "2606:4700:4700::1112",
      "2606:4700:4700::1002",
      "1.1.1.3",
      "1.0.0.3",
      "2606:4700:4700::1113",
      "2606:4700:4700::1003",
      "8.8.8.8",
      "8.8.4.4",
      "2001:4860:4860::8888",
      "2001:4860:4860::8844",
      "94.140.14.14",
      "94.140.15.15",
      "2a10:50c0::ad1:ff",
      "2a10:50c0::ad2:ff",
      "94.140.14.15",
      "94.140.15.16",
      "2a10:50c0::bad1:ff",
      "2a10:50c0::bad2:ff",
      "94.140.14.140",
      "94.140.14.141",
      "2a10:50c0::1:ff",
      "2a10:50c0::2:ff",
      "208.67.222.222",
      "208.67.220.220",
      "2620:119:35::35",
      "2620:119:53::53",
      "208.67.222.123",
      "208.67.220.123",
      "2620:119:35::123",
      "2620:119:53::123",
      "9.9.9.9",
      "149.112.112.112",
      "2620:fe::9",
      "2620:fe::fe",
      "9.9.9.11",
      "149.112.112.11",
      "2620:fe::11",
      "2620:fe::fe:11",
      "9.9.9.10",
      "149.112.112.10",
      "2620:fe::10",
      "2620:fe::fe:10",
      "77.88.8.8",
      "77.88.8.1",
      "2a02:6b8::feed:0ff",
      "2a02:6b8:0:1::feed:0ff",
      "77.88.8.88",
      "77.88.8.2",
      "2a02:6b8::feed:bad",
      "2a02:6b8:0:1::feed:bad",
      "77.88.8.7",
      "77.88.8.3",
      "2a02:6b8::feed:a11",
      "2a02:6b8:0:1::feed:a11"
    ],
    "Enabled": true,
    "Remarks": "代理海外公共DNSIP"
  },
  {
    "Id": "5596411647487815647",
    "OutboundTag": "proxy",
    "Domain": [
      "domain:cloudflare-dns.com",
      "domain:one.one.one.one",
      "domain:dns.google",
      "domain:adguard-dns.com",
      "domain:opendns.com",
      "domain:umbrella.com",
      "domain:quad9.net",
      "domain:yandex.net"
    ],
    "Enabled": true,
    "Remarks": "代理海外公共DNS域名"
  },
  {
    "Id": "4876681289880487872",
    "OutboundTag": "proxy",
    "Ip": [
      "geoip:facebook",
      "geoip:fastly",
      "geoip:google",
      "geoip:netflix",
      "geoip:telegram",
      "geoip:twitter"
    ],
    "Enabled": true,
    "Remarks": "代理IP"
  },
  {
    "Id": "4615412434582185354",
    "OutboundTag": "proxy",
    "Domain": [
      "geosite:gfw",
      "geosite:greatfire"
    ],
    "Enabled": true,
    "Remarks": "代理GFW"
  },
  {
    "Id": "4640848868119407072",
    "Port": "0-65535",
    "OutboundTag": "direct",
    "Enabled": true,
    "Remarks": "最终直连"
  }
]',11,1,0,NULL,NULL,NULL,NULL,2,0);
INSERT INTO RoutingItem (Id,Remarks,Url,RuleSet,RuleNum,Enabled,Locked,CustomIcon,CustomRulesetPath4Singbox,DomainStrategy,DomainStrategy4Singbox,Sort,IsActive) VALUES ('5423875102100240395','V3-全局(Global)','','[
  {
    "Id": "5168027058367796871",
    "Port": "443",
    "Network": "udp",
    "OutboundTag": "block",
    "Enabled": true,
    "Remarks": "阻断udp443"
  },
  {
    "Id": "5470584457682238949",
    "OutboundTag": "direct",
    "Ip": [
      "geoip:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网IP"
  },
  {
    "Id": "5463676076253178860",
    "OutboundTag": "direct",
    "Domain": [
      "geosite:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网域名"
  },
  {
    "Id": "5150934720796536365",
    "Port": "0-65535",
    "OutboundTag": "proxy",
    "Enabled": true,
    "Remarks": "最终代理"
  }
]',4,1,0,NULL,NULL,NULL,NULL,3,0);
INSERT INTO RoutingItem (Id,Remarks,Url,RuleSet,RuleNum,Enabled,Locked,CustomIcon,CustomRulesetPath4Singbox,DomainStrategy,DomainStrategy4Singbox,Sort,IsActive) VALUES ('5484053390120656192','V3-绕过大陆(Whitelist)','','[{"Id":"9000000000000000001","Network":"tcp","OutboundTag":"proxy","Domain":["domain:stripe.com","domain:stripe.network","domain:stripecdn.com","domain:stripeassets.com","domain:paypal.com","domain:paypalobjects.com","domain:paypal.me","domain:braintreegateway.com","domain:braintree-api.com","domain:braintreepayments.com","domain:squareup.com","domain:square.link","domain:cash.app","domain:adyen.com","domain:checkout.com","domain:paddle.com","domain:paddlecdn.com","domain:chargebee.com","domain:recurly.com","domain:fastspring.com","domain:2checkout.com","domain:authorize.net","domain:cybersource.com","domain:worldpay.com","domain:globalpay.com","domain:paysafe.com","domain:payoneer.com","domain:wise.com","domain:klarna.com","domain:afterpay.com","domain:affirm.com","domain:zip.co","domain:venmo.com","domain:zellepay.com","domain:googlepay.com","domain:pay.google.com","domain:payments.google.com","domain:wallet.google.com","domain:applepay.cdn-apple.com","domain:apple-pay-gateway.apple.com","domain:amazonpay.com","domain:coinbase.com","domain:coinbasecommerce.com","domain:bitpay.com","domain:crypto.com","domain:moonpay.com","domain:ramp.network","domain:transak.com","domain:mercuryo.io","domain:nowpayments.io","domain:alipay.com","domain:alipayobjects.com","domain:pay.weixin.qq.com","domain:tenpay.com"],"Enabled":true,"Remarks":"PAYMENT -> R3/R4/R5 residential only"},{"Id":"5012751897976214833","Network":"tcp","OutboundTag":"proxy","Domain":["domain:googleapis.cn","domain:gstatic.com"],"Enabled":true,"Remarks":"Google cn"},{"Id":"5171781353172117357","Port":"443","Network":"udp","OutboundTag":"block","Enabled":true,"Remarks":"阻断udp443"},{"Id":"5351326584597446307","OutboundTag":"direct","Ip":["geoip:private"],"Enabled":true,"Remarks":"绕过局域网IP"},{"Id":"4944709120230433629","OutboundTag":"direct","Domain":["geosite:private"],"Enabled":true,"Remarks":"绕过局域网域名"},{"Id":"4674820460356404651","OutboundTag":"direct","Ip":["223.5.5.5","223.6.6.6","2400:3200::1","2400:3200:baba::1","119.29.29.29","1.12.12.12","120.53.53.53","2402:4e00::","2402:4e00:1::","180.76.76.76","2400:da00::6666","114.114.114.114","114.114.115.115","114.114.114.119","114.114.115.119","114.114.114.110","114.114.115.110","180.184.1.1","180.184.2.2","101.226.4.6","218.30.118.6","123.125.81.6","140.207.198.6","1.2.4.8","210.2.4.8","52.80.66.66","117.50.22.22","2400:7fc0:849e:200::4","2404:c2c0:85d8:901::4","117.50.10.10","52.80.52.52","2400:7fc0:849e:200::8","2404:c2c0:85d8:901::8","117.50.60.30","52.80.60.30"],"Enabled":true,"Remarks":"绕过中国公共DNSIP"},{"Id":"5556941015265773597","OutboundTag":"direct","Domain":["domain:alidns.com","domain:doh.pub","domain:dot.pub","domain:360.cn","domain:onedns.net"],"Enabled":true,"Remarks":"绕过中国公共DNS域名"},{"Id":"5506657580189116110","OutboundTag":"direct","Ip":["geoip:cn"],"Enabled":true,"Remarks":"绕过中国IP"},{"Id":"5343687784658071023","OutboundTag":"direct","Domain":["geosite:cn"],"Enabled":true,"Remarks":"绕过中国域名"}]',9,1,0,NULL,NULL,NULL,NULL,1,1);
INSERT INTO RoutingItem (Id,Remarks,Url,RuleSet,RuleNum,Enabled,Locked,CustomIcon,CustomRulesetPath4Singbox,DomainStrategy,DomainStrategy4Singbox,Sort,IsActive) VALUES ('5574882148724691728','V4-绕过大陆(Whitelist)','','[{"Id":"9000000000000000001","Network":"tcp","OutboundTag":"proxy","Domain":["domain:stripe.com","domain:stripe.network","domain:stripecdn.com","domain:stripeassets.com","domain:paypal.com","domain:paypalobjects.com","domain:paypal.me","domain:braintreegateway.com","domain:braintree-api.com","domain:braintreepayments.com","domain:squareup.com","domain:square.link","domain:cash.app","domain:adyen.com","domain:checkout.com","domain:paddle.com","domain:paddlecdn.com","domain:chargebee.com","domain:recurly.com","domain:fastspring.com","domain:2checkout.com","domain:authorize.net","domain:cybersource.com","domain:worldpay.com","domain:globalpay.com","domain:paysafe.com","domain:payoneer.com","domain:wise.com","domain:klarna.com","domain:afterpay.com","domain:affirm.com","domain:zip.co","domain:venmo.com","domain:zellepay.com","domain:googlepay.com","domain:pay.google.com","domain:payments.google.com","domain:wallet.google.com","domain:applepay.cdn-apple.com","domain:apple-pay-gateway.apple.com","domain:amazonpay.com","domain:coinbase.com","domain:coinbasecommerce.com","domain:bitpay.com","domain:crypto.com","domain:moonpay.com","domain:ramp.network","domain:transak.com","domain:mercuryo.io","domain:nowpayments.io","domain:alipay.com","domain:alipayobjects.com","domain:pay.weixin.qq.com","domain:tenpay.com"],"Enabled":true,"Remarks":"PAYMENT -> R3/R4/R5 residential only"},{"Id":"5646861413181398069","Port":"443","Network":"udp","OutboundTag":"block","Enabled":true,"Remarks":"阻断udp443"},{"Id":"5632583516401281138","OutboundTag":"proxy","Domain":["geosite:google"],"Enabled":true,"Remarks":"代理Google"},{"Id":"5240886988280262114","OutboundTag":"direct","Ip":["geoip:private"],"Enabled":true,"Remarks":"绕过局域网IP"},{"Id":"5286176344312379349","OutboundTag":"direct","Domain":["geosite:private"],"Enabled":true,"Remarks":"绕过局域网域名"},{"Id":"5496591135791883189","OutboundTag":"direct","Ip":["223.5.5.5","223.6.6.6","2400:3200::1","2400:3200:baba::1","119.29.29.29","1.12.12.12","120.53.53.53","2402:4e00::","2402:4e00:1::","180.76.76.76","2400:da00::6666","114.114.114.114","114.114.115.115","114.114.114.119","114.114.115.119","114.114.114.110","114.114.115.110","180.184.1.1","180.184.2.2","101.226.4.6","218.30.118.6","123.125.81.6","140.207.198.6","1.2.4.8","210.2.4.8","52.80.66.66","117.50.22.22","2400:7fc0:849e:200::4","2404:c2c0:85d8:901::4","117.50.10.10","52.80.52.52","2400:7fc0:849e:200::8","2404:c2c0:85d8:901::8","117.50.60.30","52.80.60.30"],"Enabled":true,"Remarks":"绕过中国公共DNSIP"},{"Id":"5526969844394986150","OutboundTag":"direct","Domain":["domain:alidns.com","domain:doh.pub","domain:dot.pub","domain:360.cn","domain:onedns.net"],"Enabled":true,"Remarks":"绕过中国公共DNS域名"},{"Id":"5028075579540461954","OutboundTag":"direct","Ip":["geoip:cn"],"Enabled":true,"Remarks":"绕过中国IP"},{"Id":"5220445028413158370","OutboundTag":"direct","Domain":["geosite:cn"],"Enabled":true,"Remarks":"绕过中国域名"}]',9,1,0,NULL,NULL,NULL,NULL,4,0);
INSERT INTO RoutingItem (Id,Remarks,Url,RuleSet,RuleNum,Enabled,Locked,CustomIcon,CustomRulesetPath4Singbox,DomainStrategy,DomainStrategy4Singbox,Sort,IsActive) VALUES ('5704273787017940440','V4-黑名单(Blacklist)','','[
  {
    "Id": "5046889305740085194",
    "OutboundTag": "direct",
    "Protocol": [
      "bittorrent"
    ],
    "Enabled": true,
    "Remarks": "绕过bittorrent"
  },
  {
    "Id": "5035324274407636180",
    "OutboundTag": "proxy",
    "Domain": [
      "api.ip.sb"
    ],
    "Enabled": true,
    "Remarks": "api.ip.sb"
  },
  {
    "Id": "5160207999689475510",
    "Port": "443",
    "Network": "udp",
    "OutboundTag": "block",
    "Enabled": true,
    "Remarks": "阻断udp443"
  },
  {
    "Id": "5286498880605273901",
    "OutboundTag": "proxy",
    "Domain": [
      "geosite:google"
    ],
    "Enabled": true,
    "Remarks": "代理Google"
  },
  {
    "Id": "4710709164950192056",
    "OutboundTag": "direct",
    "Ip": [
      "geoip:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网IP"
  },
  {
    "Id": "5705402862410218633",
    "OutboundTag": "direct",
    "Domain": [
      "geosite:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网域名"
  },
  {
    "Id": "4613244302046748262",
    "OutboundTag": "proxy",
    "Ip": [
      "1.1.1.1",
      "1.0.0.1",
      "2606:4700:4700::1111",
      "2606:4700:4700::1001",
      "1.1.1.2",
      "1.0.0.2",
      "2606:4700:4700::1112",
      "2606:4700:4700::1002",
      "1.1.1.3",
      "1.0.0.3",
      "2606:4700:4700::1113",
      "2606:4700:4700::1003",
      "8.8.8.8",
      "8.8.4.4",
      "2001:4860:4860::8888",
      "2001:4860:4860::8844",
      "94.140.14.14",
      "94.140.15.15",
      "2a10:50c0::ad1:ff",
      "2a10:50c0::ad2:ff",
      "94.140.14.15",
      "94.140.15.16",
      "2a10:50c0::bad1:ff",
      "2a10:50c0::bad2:ff",
      "94.140.14.140",
      "94.140.14.141",
      "2a10:50c0::1:ff",
      "2a10:50c0::2:ff",
      "208.67.222.222",
      "208.67.220.220",
      "2620:119:35::35",
      "2620:119:53::53",
      "208.67.222.123",
      "208.67.220.123",
      "2620:119:35::123",
      "2620:119:53::123",
      "9.9.9.9",
      "149.112.112.112",
      "2620:fe::9",
      "2620:fe::fe",
      "9.9.9.11",
      "149.112.112.11",
      "2620:fe::11",
      "2620:fe::fe:11",
      "9.9.9.10",
      "149.112.112.10",
      "2620:fe::10",
      "2620:fe::fe:10",
      "77.88.8.8",
      "77.88.8.1",
      "2a02:6b8::feed:0ff",
      "2a02:6b8:0:1::feed:0ff",
      "77.88.8.88",
      "77.88.8.2",
      "2a02:6b8::feed:bad",
      "2a02:6b8:0:1::feed:bad",
      "77.88.8.7",
      "77.88.8.3",
      "2a02:6b8::feed:a11",
      "2a02:6b8:0:1::feed:a11"
    ],
    "Enabled": true,
    "Remarks": "代理海外公共DNSIP"
  },
  {
    "Id": "4960604215434160151",
    "OutboundTag": "proxy",
    "Domain": [
      "domain:cloudflare-dns.com",
      "domain:one.one.one.one",
      "domain:dns.google",
      "domain:adguard-dns.com",
      "domain:opendns.com",
      "domain:umbrella.com",
      "domain:quad9.net",
      "domain:yandex.net"
    ],
    "Enabled": true,
    "Remarks": "代理海外公共DNS域名"
  },
  {
    "Id": "4831504385750205714",
    "OutboundTag": "proxy",
    "Ip": [
      "geoip:facebook",
      "geoip:fastly",
      "geoip:google",
      "geoip:netflix",
      "geoip:telegram",
      "geoip:twitter"
    ],
    "Enabled": true,
    "Remarks": "代理IP"
  },
  {
    "Id": "5444995240927134429",
    "OutboundTag": "proxy",
    "Domain": [
      "geosite:gfw",
      "geosite:greatfire"
    ],
    "Enabled": true,
    "Remarks": "代理GFW"
  },
  {
    "Id": "4837749872924627597",
    "Port": "0-65535",
    "OutboundTag": "direct",
    "Enabled": true,
    "Remarks": "最终直连"
  }
]',11,1,0,NULL,NULL,NULL,NULL,5,0);
INSERT INTO RoutingItem (Id,Remarks,Url,RuleSet,RuleNum,Enabled,Locked,CustomIcon,CustomRulesetPath4Singbox,DomainStrategy,DomainStrategy4Singbox,Sort,IsActive) VALUES ('4707023165178790876','V4-全局(Global)','','[
  {
    "Id": "5674851272505720219",
    "Port": "443",
    "Network": "udp",
    "OutboundTag": "block",
    "Enabled": true,
    "Remarks": "阻断udp443"
  },
  {
    "Id": "4917520453867290330",
    "OutboundTag": "direct",
    "Ip": [
      "geoip:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网IP"
  },
  {
    "Id": "5731539226157466304",
    "OutboundTag": "direct",
    "Domain": [
      "geosite:private"
    ],
    "Enabled": true,
    "Remarks": "绕过局域网域名"
  },
  {
    "Id": "5714750652989596670",
    "Port": "0-65535",
    "OutboundTag": "proxy",
    "Enabled": true,
    "Remarks": "最终代理"
  }
]',4,1,0,NULL,NULL,NULL,NULL,6,0);

-- DNS settings
DELETE FROM DNSItem;
INSERT INTO DNSItem (Id,Remarks,Enabled,CoreType,UseSystemHosts,NormalDNS,TunDNS,DomainStrategy4Freedom,DomainDNSAddress) VALUES ('5162237698108335651','V2ray',0,2,0,'',NULL,'','');
INSERT INTO DNSItem (Id,Remarks,Enabled,CoreType,UseSystemHosts,NormalDNS,TunDNS,DomainStrategy4Freedom,DomainDNSAddress) VALUES ('5566341701928963695','sing-box',0,24,0,'','','','');
