# Prompt — Step 4.4: Kubernetes manifests + first deploy

> Paste this entire block into a fresh Claude Code session when starting Step 4.4.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on D-22 (single GCP project for dev), D-14 (no PgBouncer), and environment variables.
3. Read `docs/architecture.md` "Deployment topology" and Appendix A.2 (Cloud SQL Auth Proxy sidecar).
4. Read `BUILD_PLAN.md` Step 4.4 in full.
5. Read this prompt fully and confirm scope.

This step depends on:

- Step 3.4 GCP dev environment ready (Cloud SQL provisioned, GKE cluster up, Artifact Registry created, service account with Workload Identity binding, Secret Manager secrets).
- Step 4.1 DDLs applied to Cloud SQL.
- Step 4.2 Dockerfile + image built.
- Step 4.3 Image pushed to Artifact Registry.

If any of these aren't done, stop and confirm.

---

## Step ID and intent

**Step 4.4** — Kubernetes manifests + first deploy.

Two parts:

- **Part A (CLAUDE_CODE):** Write Kubernetes manifests for the dev environment.
- **Part B (HUMAN, with CLAUDE_CODE assistance):** Execute kubectl commands to deploy. Verify endpoints respond.

This is a HYBRID step. **Claude Code writes the YAML; the user runs kubectl. Do not run kubectl yourself.** Ask before issuing any GCP/Kubernetes command. Output explicit commands for the user to run.

The first cloud deploy. Expect debugging — first-time GKE work always uncovers gotchas. Budget 90-120 min.

---

## Scope in (Part A — Claude Code writes)

### File 1: `k8s/dev/namespace.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: admin-backend-dev
  labels:
    app.kubernetes.io/name: admin-backend
    environment: dev
```

### File 2: `k8s/dev/serviceaccount.yaml`

Workload Identity binding. The Kubernetes service account is annotated with the GCP service account email (provided by GCP-helper).

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: admin-backend-sa
  namespace: admin-backend-dev
  annotations:
    iam.gke.io/gcp-service-account: admin-backend-sa@<project-id>.iam.gserviceaccount.com
```

User must replace `<project-id>` with actual GCP project ID for dev (e.g., `ithina-admin-dev`). Document in a header comment.

### File 3: `k8s/dev/configmap.yaml`

Non-secret configuration.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: admin-backend-config
  namespace: admin-backend-dev
data:
  ENVIRONMENT: "development"
  APP_REGION: "US"
  LOG_LEVEL: "INFO"
  AUTH_CLIENT_MODE: "STUB"  # toggled to AUTH0 at Step 8.3
  JWT_ISSUER: "https://stub-issuer.local/"
  JWT_AUDIENCE: "https://api.ithina.com"
  TOKEN_DEFAULT_TTL_SECONDS: "3600"
  SERVICE_NAME: "admin-backend"
  CORS_ALLOWED_ORIGINS: "https://admin-dev.ithina.com,http://localhost:5173"
  # When AUTH_CLIENT_MODE=STUB, JWT_PUBLIC_KEY_PATH points at a key
  # mounted from a Secret (see deployment.yaml).
  JWT_PUBLIC_KEY_PATH: "/keys/jwt_public.pem"
```

### File 4: `k8s/dev/deployment.yaml`

Main app deployment with Cloud SQL Auth Proxy sidecar.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: admin-backend
  namespace: admin-backend-dev
  labels:
    app.kubernetes.io/name: admin-backend
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: admin-backend
  template:
    metadata:
      labels:
        app.kubernetes.io/name: admin-backend
    spec:
      serviceAccountName: admin-backend-sa
      containers:
        - name: admin-backend
          image: <region>-docker.pkg.dev/<project-id>/admin-backend/admin-backend:dev-latest
          ports:
            - name: http
              containerPort: 8000
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: admin-backend-db
                  key: url
          envFrom:
            - configMapRef:
                name: admin-backend-config
          volumeMounts:
            - name: jwt-keys
              mountPath: /keys
              readOnly: true
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: /v1/health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /v1/health
              port: http
            initialDelaySeconds: 15
            periodSeconds: 30
        - name: cloud-sql-proxy
          image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.x
          args:
            - "--structured-logs"
            - "--port=5432"
            - "<project-id>:<region>:<instance-name>"
          securityContext:
            runAsNonRoot: true
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 100m
              memory: 128Mi
      volumes:
        - name: jwt-keys
          secret:
            secretName: admin-backend-jwt-keys
            items:
              - key: jwt_public.pem
                path: jwt_public.pem
```

Notes:

- Image tag uses `dev-latest`; user updates to a specific SHA tag for reproducibility once they're comfortable.
- Cloud SQL Auth Proxy listens on `localhost:5432`. The `DATABASE_URL` secret value should reference `localhost:5432` for the proxy.
- JWT public key is mounted from Secret Manager (or a Kubernetes Secret synced from Secret Manager). User creates this Secret separately.
- Both readiness and liveness probes hit `/v1/health` (which doesn't require auth).

Add header comments in the file documenting placeholders the user must fill in:

```yaml
# REQUIRED REPLACEMENTS BEFORE APPLYING:
#   <project-id>: GCP project ID (e.g., ithina-admin-dev)
#   <region>: GCP region (e.g., us-central1)
#   <instance-name>: Cloud SQL instance name (e.g., admin-master)
```

### File 5: `k8s/dev/service.yaml`

ClusterIP service:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: admin-backend
  namespace: admin-backend-dev
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: admin-backend
  ports:
    - name: http
      port: 80
      targetPort: 8000
```

### File 6: `k8s/dev/ingress.yaml`

GCE ingress with managed cert:

```yaml
apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: admin-backend-cert
  namespace: admin-backend-dev
spec:
  domains:
    - admin-dev.ithina.com  # placeholder; user confirms with GCP-helper
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: admin-backend
  namespace: admin-backend-dev
  annotations:
    kubernetes.io/ingress.global-static-ip-name: admin-backend-dev-ip
    networking.gke.io/managed-certificates: admin-backend-cert
    kubernetes.io/ingress.class: "gce"
spec:
  rules:
    - host: admin-dev.ithina.com
      http:
        paths:
          - path: /*
            pathType: ImplementationSpecific
            backend:
              service:
                name: admin-backend
                port:
                  number: 80
```

User must:

- Reserve a global static IP in GCP (`gcloud compute addresses create admin-backend-dev-ip --global`).
- Configure DNS to point `admin-dev.ithina.com` at that IP.
- Confirm the actual hostname with GCP-helper.

### File 7: `k8s/dev/README.md`

Brief deploy guide:

```markdown
# k8s/dev — Admin backend Kubernetes manifests for the dev environment

## Required replacements before applying

Edit each YAML file and replace placeholders:

- `<project-id>` → GCP project ID (e.g., `ithina-admin-dev`)
- `<region>` → GCP region (e.g., `us-central1`)
- `<instance-name>` → Cloud SQL instance name
- `admin-dev.ithina.com` → confirmed dev hostname

## Required Kubernetes Secrets (created out-of-band)

Two secrets must exist in the `admin-backend-dev` namespace before applying:

1. `admin-backend-db` — contains key `url` with the Postgres connection string
   (pointing at `localhost:5432` since traffic flows through the Cloud SQL Auth
   Proxy sidecar).
2. `admin-backend-jwt-keys` — contains key `jwt_public.pem` with the stub JWT
   public key (during build phase). Replace with Auth0 JWKS URL config at
   Step 8.3.

Create them with:
```
kubectl create secret generic admin-backend-db \
  --namespace admin-backend-dev \
  --from-literal=url='postgresql+psycopg://USER:PASS@localhost:5432/<DATABASE_NAME>'

kubectl create secret generic admin-backend-jwt-keys \
  --namespace admin-backend-dev \
  --from-file=jwt_public.pem=keys/jwt_public.pem
```

## Apply order

```
kubectl apply -f namespace.yaml
kubectl apply -f serviceaccount.yaml
kubectl apply -f configmap.yaml
# Create secrets (above)
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f ingress.yaml
```

## Verify

```
kubectl get pods -n admin-backend-dev -w  # wait for Running
kubectl logs -n admin-backend-dev <pod-name> -c admin-backend
kubectl get ingress -n admin-backend-dev   # note external IP
curl https://admin-dev.ithina.com/v1/health
```
```

---

## Scope out (Part A)

- Production manifests (Step 8.x).
- HPA (Horizontal Pod Autoscaler) — add later if load demands.
- NetworkPolicy — defer to post-launch.
- Pod disruption budgets, anti-affinity rules — overkill for v0 dev.
- ArgoCD GitOps — explicitly deferred.

---

## Scope (Part B — what to ASK the user to run)

After writing the manifests, hand control back to the user with a numbered command list. **Do not execute any of these commands.**

1. Replace placeholders in YAML files (project-id, region, instance-name, hostname).
2. Authenticate to GKE: `gcloud container clusters get-credentials <cluster-name> --region <region> --project <project-id>`.
3. Reserve static IP: `gcloud compute addresses create admin-backend-dev-ip --global --project <project-id>`.
4. Create Kubernetes secrets (commands from k8s/dev/README.md).
5. Apply manifests in order (commands from k8s/dev/README.md).
6. Watch pod startup: `kubectl get pods -n admin-backend-dev -w`. Expect Pending → ContainerCreating → Running.
7. Check logs: `kubectl logs -n admin-backend-dev <pod-name> -c admin-backend`.
8. Get ingress IP: `kubectl get ingress -n admin-backend-dev`. (Allow up to 10-15 min for managed cert provisioning.)
9. Configure DNS: point `admin-dev.ithina.com` at the static IP.
10. Test: `curl https://admin-dev.ithina.com/v1/health`. Expect `{"status":"ok"}`.
11. Mint a test JWT and test a protected endpoint: `curl -H "Authorization: Bearer <jwt>" https://admin-dev.ithina.com/v1/tenants`.
12. Verify cross-tenant isolation: mint JWTs for two different tenants; confirm each only sees their own data.

For each step, the user will likely report back with output. Be ready to debug:

- Pod CrashLoopBackOff: check logs for missing env var, can't connect to DB, JWT key path wrong.
- Cloud SQL Auth Proxy fails: project-id/region/instance-name typo.
- 401 from `/v1/tenants` with valid JWT: check ConfigMap values match what stub auth client expects.
- ManagedCertificate stuck in Provisioning: DNS hasn't propagated yet, or hostname doesn't resolve to the static IP.

---

## Scope out (Part B)

- Production deploy.
- Auth0 swap.
- Multi-region.

---

## Implementation hints

### Workload Identity

The serviceaccount.yaml annotation MUST match the GCP service account email exactly. Wrong format here = pods can't access Secret Manager at runtime, leading to confusing 500s.

Verify with the user: GCP-helper provided the GCP service account email. Confirm it before applying.

### Cloud SQL Auth Proxy version

Use `cloud-sql-proxy:2.x` (v2 is current generation; v1 is deprecated). Pin to a specific minor version (`2.5.0` or similar) to avoid surprise updates.

### Image pull from Artifact Registry

GKE Autopilot has built-in support for pulling from Artifact Registry IF the GKE node service account has `artifactregistry.reader` role. GCP-helper should have set this up. If pods get `ErrImagePull`, this is the cause.

### Health probe configuration

Initial delay: 5 seconds for readiness. Pod takes ~3-5s to start uvicorn + load auth client + connect to DB via proxy. If probe is too aggressive, pods cycle.

### Ingress takes time

GCE ingress with managed cert can take 10-15 min to fully provision (cert validation, LB config, IP attachment). Don't panic if it's not immediate.

---

## Acceptance criteria

- All 7 files written per scope (6 YAML + 1 README).
- User has applied manifests; pods are Running; cert is Active; ingress IP is responsive.
- All current endpoints respond via load balancer URL:
  - `GET /v1/health` → 200.
  - `GET /v1/tenants` (with JWT) → 200.
  - `GET /v1/tenants` (without JWT) → 401.
- Cross-tenant isolation verified in cloud:
  - Tenant A JWT against `/v1/tenants` returns only tenant A.
  - Tenant B JWT against `/v1/tenants` returns only tenant B.
- Logs visible in Cloud Logging with parsed JSON fields.
- Frontend dev has been notified with the dev URL and OpenAPI spec link.

---

## Stop and ask if

- GCP-helper has not provided required values (project ID, region, Cloud SQL instance name, GCP service account email, hostname).
- Pod logs show an error you can't immediately resolve. Don't try random fixes; surface to the user.
- Workload Identity isn't working as expected (pod can't access Secret Manager). This is GCP-helper's domain to fix.
- The ManagedCertificate stays in Provisioning longer than 30 min. DNS or routing issue.

---

## What to report at end

- Files created.
- For each kubectl command the user ran: result observed (Pending/Running, errors, etc.).
- Final URL where the backend is reachable.
- Test results: 401 without JWT, 200 with JWT, cross-tenant isolation confirmed.
- Any debugging done; what fixed it.
- Confirmation message to send to frontend dev (URL + OpenAPI spec link).

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 4.4: Kubernetes manifests + first deploy to GCP dev

- k8s/dev/ manifests: namespace, serviceaccount, configmap, deployment, service, ingress
- Cloud SQL Auth Proxy as sidecar
- Workload Identity binding for Secret Manager access
- All endpoints verified responding via dev URL
- Cross-tenant isolation verified in cloud
- Frontend dev notified with dev URL"
```

Ask user "Run? yes / no / edit message".

---

## End of prompt
