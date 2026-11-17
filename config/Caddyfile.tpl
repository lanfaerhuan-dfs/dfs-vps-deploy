{
	admin off
	auto_https disable_redirects
	servers {
		protocols h1 h2
	}
}

http://:80 {
	respond 404
}

https://$domain:$https_port {
	tls {
		issuer acme {
			disable_tlsalpn_challenge
		}
	}
	@subscription {
		method GET HEAD
		path $route
	}
	handle @subscription {
		header {
			Cache-Control "private, no-store"
			X-Content-Type-Options "nosniff"
			Content-Type "application/yaml; charset=utf-8"
		}
		root * $www_root
		rewrite * /subscription.yaml
		file_server
	}
	handle {
		respond 404
	}
}
