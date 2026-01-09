# job-manager-operator

## Install

```bash
python3 -m venv env
source env/bin/activate
pip install -e .
```

## Run

Apply crds:
```bash
kubectl apply -f chart/templates/crds.yaml
```

Run operator:
```bash
kopf run kalavai_job_operator/operator.py
```

Deploy test job:

```bash
kubectl apply -f test/job.yaml
```

## Install with helm

### Requirements

Install Flux (only sourceController and helmController are required):

```bash
helm install flux oci://ghcr.io/fluxcd-community/charts/flux2 --create-namespace -n flux-system --version 2.16.2 --set helmController.create=true --set sourceController.create=true --set imageAutomationController.create=false --set imageReflectionController.create=false --set kustomizeController.create=false --set notificationController.create=false
```

Install volcano-sh scheduler:

```bash
helm install volcano volcano-sh/volcano --version "1.13.0" --create-namespace -n volcano-system
```

# Install from local repo

```bash
helm install my-release ./chart
```

## BUILD

Build docker image:
```bash
docker build -t kalavai/kalavai-job-operator:latest .
docker push kalavai/kalavai-job-operator:latest
```


## TODO

- Reference to job templates documentation repo