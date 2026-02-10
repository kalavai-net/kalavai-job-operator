# job-manager-operator

Kubernetes operator to manage KalavaiJobs. A KalavaiJob is a convenient CRD that deploys [kalavai templates](https://github.com/kalavai-net/kalavai-templates) in Kubernetes.


## Requirements

Install Flux (only sourceController and helmController are required):

```bash
helm install flux oci://ghcr.io/fluxcd-community/charts/flux2 --create-namespace -n flux-system --version 2.16.2 --set helmController.create=true --set sourceController.create=true --set imageAutomationController.create=false --set imageReflectionController.create=false --set kustomizeController.create=false --set notificationController.create=false
```

Install volcano-sh scheduler:

```bash
helm install volcano volcano-sh/volcano --version "1.13.0" --create-namespace -n volcano-system
```

## Install from local repo

```bash
helm install my-release ./chart
```

## Install from published chart

```bash
helm repo add kalavai-job-operator https://kalavai-net.github.io/kalavai-job-operator/
helm repo update

helm install my-release kalavai-job-operator
```

## Test

Deploy test job:

```bash
kubectl apply -f test/job.yaml
```

## BUILD

Build docker image:
```bash
docker build -t ghcr.io/kalavai-net/kalavai-job-operator:latest .
docker push ghcr.io/kalavai-net/kalavai-job-operator:latest
```

## Develop

Configure environment

```bash
python3 -m venv env
source env/bin/activate
pip install -e .
```

Apply crds:
```bash
kubectl apply -f chart/templates/crds.yaml
```

Run operator:
```bash
kopf run kalavai_job_operator/job_operator.py
```
