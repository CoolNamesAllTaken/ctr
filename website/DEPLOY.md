# Deploying to Google Cloud

Every push to `main` deploys to the `caltreat` Google Cloud project, once the
checks in `.github/workflows/ci.yml` pass. This guide covers the one-time
setup that makes that work, then how deploys run and how to connect the
domains. Plan on about an hour for the setup, most of it waiting for the
database.

> [!NOTE]
> The employee portal (`/employee`) is behind one shared passphrase, not
> per-person accounts. Anyone who has it can add riders, change card balances,
> record taps and post alerts. Change it (step 4) if it gets around.

## What you'll end up with

```
 ctr.arborhalloween.com ──┐
                          ├─▶ Cloud Run: ctr-web ──/api──▶ Cloud Run: ctr-api ──▶ Cloud SQL
 chippercard.com ─────────┘     (Next.js, both sites)        (Django)              (Postgres 18)
```

- **ctr-web** runs the production build of `frontend/`. `src/proxy.ts` picks
  the site from the domain, exactly as in development.
- **ctr-api** runs Django under gunicorn. The Django admin lives on its URL
  (`…/admin/`).
- **ctr-manage** is a Cloud Run job using the Django image. Deploys use it to
  run migrations, and you can use it for other `manage.py` commands.
- GitHub Actions builds the images, pushes them to Artifact Registry and
  deploys. It signs in to Google Cloud with
  [Workload Identity Federation](https://github.com/google-github-actions/auth#preferred-direct-workload-identity-federation),
  so there are no keys or repo secrets to manage.
- Secrets live in Secret Manager.

Both Cloud Run services scale to zero when idle, so the always-on cost is the
database. The smallest Cloud SQL tier used here is roughly $10 a month; check
the [pricing calculator](https://cloud.google.com/products/calculator) for
current numbers.

## Before you start

You need:

- The `caltreat` Google Cloud project with billing enabled, and Owner on it.
- The [gcloud CLI](https://cloud.google.com/sdk/docs/install), logged in with
  `gcloud auth login`.

The project ID, region and service names below also appear in the `deploy`
job's `env` in `.github/workflows/ci.yml`. If you change one, change both.

## 1. Set some variables

Later steps use these. If you close your terminal partway through, paste this
block again before carrying on.

```sh
export PROJECT_ID=caltreat
export REGION=us-west1
export GITHUB_REPO=CoolNamesAllTaken/ctr

gcloud config set project $PROJECT_ID
gcloud config set run/region $REGION

export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
export GITHUB_REPO_ID=$(gh api repos/$GITHUB_REPO --jq .id)
export API_SA=ctr-api@$PROJECT_ID.iam.gserviceaccount.com
export WEB_SA=ctr-web@$PROJECT_ID.iam.gserviceaccount.com
export DEPLOY_SA=ctr-deploy@$PROJECT_ID.iam.gserviceaccount.com
```

`us-west1` (Oregon) is close to the Bay Area and supports the custom domain
mappings used in step 10. If you don't have the `gh` CLI, the repo ID is the
`id` field at `https://api.github.com/repos/CoolNamesAllTaken/ctr`.

## 2. Turn on the services

```sh
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com
```

## 3. Create the database

This takes several minutes.

```sh
gcloud sql instances create ctr-db \
  --database-version=POSTGRES_18 \
  --edition=ENTERPRISE \
  --tier=db-f1-micro \
  --region=$REGION

gcloud sql databases create ctr --instance=ctr-db

DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
gcloud sql users create ctr --instance=ctr-db --password="$DB_PASSWORD"
```

`--edition=ENTERPRISE` is what allows the small shared-core `db-f1-micro`
tier. When you need more, change the tier with
`gcloud sql instances patch ctr-db --tier=...`.

## 4. Store the secrets

```sh
printf %s "$DB_PASSWORD" | gcloud secrets create postgres-password --data-file=-

python3 -c "import secrets; print(secrets.token_urlsafe(50), end='')" \
  | gcloud secrets create django-secret-key --data-file=-
```

Then choose the passphrase employees log in to the portal with:

```sh
read -rs EMPLOYEE_PASSPHRASE   # type the passphrase, then Enter
printf %s "$EMPLOYEE_PASSPHRASE" | gcloud secrets create employee-passphrase --data-file=-
```

To change it later, add a new version with
`gcloud secrets versions add employee-passphrase --data-file=-` the same way,
then redeploy (step 8). Changing it logs everyone out.

## 5. Create the image repository

```sh
gcloud artifacts repositories create ctr \
  --repository-format=docker --location=$REGION
```

Every deploy adds an image to it, tagged with the commit. They're small, but
you can add a
[cleanup policy](https://cloud.google.com/artifact-registry/docs/repositories/cleanup-policy)
to keep only the recent ones.

## 6. Create the service accounts

Each service runs as its own account. Django's can reach the database and read
the secrets. The frontend's can only read the employee passphrase.

```sh
gcloud iam service-accounts create ctr-api --display-name="ctr Django API"
gcloud iam service-accounts create ctr-web --display-name="ctr Next.js frontend"

for role in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:$API_SA --role=$role --condition=None
done

gcloud secrets add-iam-policy-binding employee-passphrase \
  --member=serviceAccount:$WEB_SA --role=roles/secretmanager.secretAccessor
```

A new service account can take a few seconds to become usable. If a binding
says the account doesn't exist, wait a moment and run it again.

## 7. Let GitHub Actions deploy

GitHub Actions deploys as a third account, `ctr-deploy`. It can push images,
manage Cloud Run, and run the services as the two accounts above:

```sh
gcloud iam service-accounts create ctr-deploy --display-name="ctr GitHub deploys"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$DEPLOY_SA --role=roles/run.admin --condition=None
gcloud artifacts repositories add-iam-policy-binding ctr --location=$REGION \
  --member=serviceAccount:$DEPLOY_SA --role=roles/artifactregistry.writer
for sa in $API_SA $WEB_SA; do
  gcloud iam service-accounts add-iam-policy-binding $sa \
    --member=serviceAccount:$DEPLOY_SA --role=roles/iam.serviceAccountUser
done
```

`run.admin` rather than the narrower `run.developer` is what lets the
workflow make the services public (`--allow-unauthenticated`).

Then let workflow runs on `main` of this repo, and nothing else, act as
`ctr-deploy`. The repo is matched by ID, not name, so a repo created later
under the same name can't deploy.

```sh
gcloud iam workload-identity-pools create github \
  --location=global --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc ctr-repo \
  --location=global \
  --workload-identity-pool=github \
  --display-name="ctr repo" \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref \
  --attribute-condition="assertion.repository_id == '$GITHUB_REPO_ID' && assertion.ref == 'refs/heads/main'"

gcloud iam service-accounts add-iam-policy-binding $DEPLOY_SA \
  --role=roles/iam.workloadIdentityUser \
  --member=principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository_id/$GITHUB_REPO_ID
```

## 8. Deploy

Push or merge to `main`. Alternatively, open the repo's **Actions** tab,
choose **CI**, and click **Run workflow** on `main`. The `deploy` job then:

1. Builds the Django and Next.js images from the commit and pushes them,
   tagged with its SHA.
2. Points the `ctr-manage` job at the new Django image and runs
   `manage.py migrate`. If that fails, the deploy stops there and the old
   code keeps running.
3. Deploys `ctr-api`, then `ctr-web`.

The first run creates the services and the job. Each later run applies the
full configuration in the workflow again, so to change an environment
variable or a setting, edit the workflow, not the Cloud Console.

Because migrations run while the old code is still serving, keep them
backwards compatible. For example, add a column in one deploy and only start
requiring it in the next.

## 9. After the first deploy

Create an admin user. The password goes through Secret Manager so that it
never appears in the job's settings or history:

```sh
read -rs ADMIN_PASSWORD   # type the password, then Enter
printf %s "$ADMIN_PASSWORD" | gcloud secrets create django-superuser-password --data-file=-

gcloud run jobs update ctr-manage \
  --update-secrets=DJANGO_SUPERUSER_PASSWORD=django-superuser-password:latest
gcloud run jobs execute ctr-manage --wait \
  --args=manage.py,createsuperuser,--noinput,--username=admin,--email=you@example.com

gcloud run jobs update ctr-manage --remove-secrets=DJANGO_SUPERUSER_PASSWORD
gcloud secrets delete django-superuser-password --quiet
```

Optionally, load the starter train cars, stations and kiosks:

```sh
gcloud run jobs execute ctr-manage --wait --args=manage.py,load_starter_data
```

Then check it worked. List the two URLs with:

```sh
gcloud run services list --format='table(metadata.name, status.url)'
```

- The `ctr-web` URL shows the Caltreat site with real schedule data.
- The `ctr-api` URL plus `/admin/` shows a styled Django admin you can log in
  to.

If a job fails, `gcloud run jobs executions list --job=ctr-manage` shows its
executions and the Cloud Console links to their logs. A failed deploy shows
its logs in the GitHub Actions run.

To roll back, revert the commit on `main`. For something faster, open the
service in the Cloud Console, go to **Revisions**, and send traffic to the
previous one. The next deploy from `main` replaces that.

## 10. Connect the domains

chippercard.com serves the Chipper Card site, and ctr.arborhalloween.com the
Caltreat site.

Domain mappings are in gcloud's beta commands. Install those once:

```sh
gcloud components install beta
```

### chippercard.com

Verify you own the domain. This opens Google Search Console, which asks you
to add a TXT record to DNS. Sign in to it with the same Google account gcloud
uses, or the mapping in the next step is refused.

```sh
gcloud domains verify chippercard.com
```

Once Search Console shows the domain as verified, map both hostnames to
`ctr-web`:

```sh
for domain in chippercard.com www.chippercard.com; do
  gcloud beta run domain-mappings create --service=ctr-web --domain=$domain
done
```

Each mapping lists the DNS records it needs under `resourceRecords`:

```sh
gcloud beta run domain-mappings describe --domain=chippercard.com
gcloud beta run domain-mappings describe --domain=www.chippercard.com
```

chippercard.com's DNS is at GoDaddy. On the domain's **DNS** page there:

1. Delete the existing `A` records for `@`. They point at GoDaddy's parking
   or forwarding servers, and if any are left some visitors land there and
   Google can't issue the certificate. If the domain has **Forwarding** set
   up, remove that too.
2. Add every `A` and `AAAA` record the bare domain's mapping lists, with the
   name `@`.
3. Change the `www` record to a `CNAME` to `ghs.googlehosted.com`.

Google issues HTTPS certificates once DNS resolves. That usually takes 15
minutes to an hour and can take up to a day. The mapping's `status` in the
`describe` output shows how far along it is.

The Chipper hostnames are listed in `frontend/src/proxy.ts`. Every other
hostname gets the Caltreat site. If you add another Chipper hostname, add it
to that list.

Once it works, check:

- `https://chippercard.com` and `https://www.chippercard.com`: the Chipper
  Card home page, with How it works and Get a card.
- `https://chippercard.com/schedule` and `https://chippercard.com/employee`:
  404s, because Caltreat pages aren't served on the Chipper domain.
- `https://chippercard.com/api/health/`: a 404, because the API isn't either.

### ctr.arborhalloween.com

Every hostname not in the Chipper list gets the Caltreat site, so this needs
no code change.

Verify you own the domain, as for chippercard.com. Verifying
arborhalloween.com covers all its subdomains. If you can only change the
`ctr` record, verify `ctr.arborhalloween.com` instead.

```sh
gcloud domains verify arborhalloween.com
```

Then map it and list the record it needs:

```sh
gcloud beta run domain-mappings create --service=ctr-web --domain=ctr.arborhalloween.com
gcloud beta run domain-mappings describe --domain=ctr.arborhalloween.com
```

arborhalloween.com's DNS is at Cloudflare, which already has a `ctr` record.
On the domain's **DNS** page there, change that record to a `CNAME` to
`ghs.googlehosted.com` and set its proxy status to **DNS only** (the grey
cloud). If Cloudflare proxies it, visitors reach Cloudflare instead of Google
and Google can't issue the certificate.

Once the certificate is issued, `https://ctr.arborhalloween.com` should show
the Caltreat home page, with Schedules, Live Map and Alerts.

### When to use a load balancer instead

Cloud Run domain mappings are a preview feature. Google doesn't recommend them
for production, and they add some latency. When the site needs to be
dependable, put a
[global external Application Load Balancer](https://cloud.google.com/run/docs/mapping-custom-domains)
in front of `ctr-web` instead. It costs a monthly fee but supports every
region, Cloud CDN and Cloud Armor. It forwards the original `Host` header, so
`proxy.ts` keeps working. Avoid putting Firebase Hosting in front: it rewrites
`Host` to the Cloud Run URL, and every request would get the Caltreat site.
