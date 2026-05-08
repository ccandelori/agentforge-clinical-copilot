# AgentForge Deployment Snippets

Drop-in configuration files that wire AgentForge into a host's web
server and process supervisor. None of these are required for the
module to function (the PHP entry points work via direct module-path
URLs and the sidecar talks to OpenEMR over the docker network) — they
add convenience URLs and defense-in-depth network filtering.

## `apache-agentforge.conf`

Apache 2.4 include. Provides two things:

1. **Clean URL** — `/agentforge/turn` rewrites to the long PHP module
   path. Lets the chat-panel (or any HTTP client) target a stable,
   short URL.
2. **Defense-in-depth on internal endpoints** — restricts
   `/interface/modules/custom_modules/oe-module-agentforge/public/internal/*`
   to RFC-1918 private network ranges plus loopback. Public-internet
   traffic gets a 403 at the network edge before PHP runs. The
   JWT check inside each endpoint is still the primary security
   gate; this is a second layer.

### Install (dev-easy / Alpine container)

```bash
# From the repo root, with development-easy stack running:
docker cp interface/modules/custom_modules/oe-module-agentforge/deploy/apache-agentforge.conf \
    development-easy-openemr-1:/etc/apache2/conf.d/agentforge.conf
docker exec development-easy-openemr-1 httpd -t        # syntax check
docker exec development-easy-openemr-1 httpd -k graceful  # zero-downtime reload
```

### Install (Debian / Ubuntu host)

```bash
sudo cp interface/modules/custom_modules/oe-module-agentforge/deploy/apache-agentforge.conf \
    /etc/apache2/conf-available/agentforge.conf
sudo a2enconf agentforge
sudo apachectl configtest && sudo systemctl reload apache2
```

### Install (production droplet)

The droplet's deploy script (`scripts/deploy-droplet.sh`) does NOT
currently push this file. To add it:

```bash
ssh root@<droplet> 'mkdir -p /etc/apache2/conf.d/'
scp interface/modules/custom_modules/oe-module-agentforge/deploy/apache-agentforge.conf \
    root@<droplet>:/etc/apache2/conf.d/agentforge.conf
ssh root@<droplet> 'docker exec openemr httpd -t && docker exec openemr httpd -k graceful'
```

(Or extend `scripts/deploy-droplet.sh` to copy + reload as part of
the standard deploy flow.)

### Verify

```bash
# Clean URL — should be 405 (POST endpoint, GET not allowed) but reachable:
wget -SqO- http://localhost:8300/agentforge/turn 2>&1 | grep '^  HTTP'
# Expected: HTTP/1.1 405 Method Not Allowed (or 401 if Authorization header missing)

# Internal endpoint over loopback — should reach PHP (401 because no JWT):
wget -SqO- http://localhost:8300/interface/modules/custom_modules/oe-module-agentforge/public/internal/medications.php 2>&1 | grep '^  HTTP'
# Expected: HTTP/1.1 401 Unauthorized

# Internal endpoint from a public IP would return:
# Expected: HTTP/1.1 403 Forbidden  (Apache, before PHP runs)
```

## Why no Nginx config

OpenEMR ships with Apache (both upstream and on the production droplet).
A Nginx alternative would be additional surface area for a deployment
shape no current operator uses. Skipped intentionally; Task 36.3 left
as wontfix in the project tracker.

## Why no Caddy config

Same reasoning. The original task spec mentioned Caddy as an example,
but the project's actual deployment shape is Apache. Add a Caddyfile
here later if the deployment surface ever changes.

## What this does NOT do

- Does not reverse-proxy `/agentforge/turn` directly to the sidecar.
  PHP must remain in the path because `AgentProxyController` is what
  bridges the OpenEMR session into a JWT for the sidecar.
- Does not add SSL termination — that's the existing OpenEMR Apache
  config's job. This snippet layers on top.
- Does not configure rate limiting. If the agent endpoint becomes a
  spam vector, add `mod_qos` or front Apache with Cloudflare.
