import os
import uuid

import kopf
from kubernetes import client, config


TEMPLATE_LABEL = os.getenv("TEMPLATE_LABEL", "kalavai.job.name")
JOB_LABEL_KEY = "kalavai.job.name"
HELM_PLURAL = "helmreleases"
HELM_API_VERSION = "v2"
HELM_GROUP = "helm.toolkit.fluxcd.io"


def create(spec, name, namespace, patch, logger):
    # 1. Extract the list of key-value pairs from the spec
    values = spec.get('template', {}).get("values", {}) 
    chart = spec.get('template', {}).get("chart", None)
    version = spec.get('template', {}).get("version", None)
    priority_class = spec.get('priorityClassName', None)
    node_selectors = spec.get('nodeSelectors', None)
    node_selectors_ops = spec.get('nodeSelectorsOps', "OR")
    
    if not values:
        logger.info(f"KalavaiJob '{name}' created with no template.values")
        return

    logger.info(f"Deploying KalavaiJob '{name}' in namespace '{namespace}'")

    # inject job id to values
    job_id = str(uuid.uuid4())
    if "jobId" in values:
        logger.info("JobId property found in provided values. It will be overwritten.")
    values["jobId"] = job_id

    # inject system values
    if "system" in values:
        logger.info("'System' property found in provided values. It will be overwritten")
    values["system"] = {
        "priorityClassName": priority_class,
        "nodeSelectors": node_selectors,
        "nodeSelectorsOps": node_selectors_ops
    }
    # Deploy helm template chart
    helm_release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": name,
            "labels": {
                JOB_LABEL_KEY: job_id
            }
        },
        "spec": {
            "interval": "10m",
            "chart": {
                "spec": {
                    "chart": chart,
                    "version": version,
                    "sourceRef": {
                        "kind": "HelmRepository",
                        "name": "kalavai-templates",
                        "namespace": "default",
                    }
                }
            },
            "values": values
        }
    }

    # kopf.adopt() makes the KalavaiJob the owner of 'HelmRelease'
    kopf.adopt(helm_release)
    
    # Use the custom object API to create it
    custom_api = client.CustomObjectsApi()
    result = custom_api.create_namespaced_custom_object(
        group=HELM_GROUP, version=HELM_API_VERSION, 
        namespace=namespace, plural=HELM_PLURAL, body=helm_release
    )
    patch.status['jobId'] = job_id

    return {'status': 'synced', 'success': ""}

def delete(status, namespace, logger):
    api = client.CustomObjectsApi()

    job_id = status.get('jobId', None)

    if job_id is None:
        logger.warning(f"jobId not found, cannot delete")
        return
    
    label_selector = f"{JOB_LABEL_KEY}={job_id}"
    
    # 2. Find the objects
    try:
        response = api.list_namespaced_custom_object(
            group=HELM_GROUP,
            version=HELM_API_VERSION,
            namespace=namespace,
            plural=HELM_PLURAL,
            label_selector=label_selector
        )
        
        items = response.get('items', [])
        logger.info(f"Found {len(items)} resources to delete.")

        # 3. Delete each item
        for item in items:
            name = item['metadata']['name']
            api.delete_namespaced_custom_object(
                group=HELM_GROUP,
                version=HELM_API_VERSION,
                namespace=namespace,
                plural=HELM_PLURAL,
                name=name,
                body=client.V1DeleteOptions() # Required for some versions
            )
            logger.info(f"Deleted resource: {name}")

    except client.exceptions.ApiException as e:
        logger.warning(f"Exception when calling CustomObjectsApi: {e}")


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

@kopf.on.create('kalavai.net', 'v1', 'kalavaijobs')
def create_fn(spec, name, namespace, patch, logger, **kwargs):
    """
    Triggered when a new KalavaiJob object is created.

    Deploy job with helm

    TODO:
    - check if name already used
    - graceful failure if helm release fails
    """
    result = create(
        spec=spec,
        name=name,
        namespace=namespace,
        patch=patch,
        logger=logger
    )

    return result

@kopf.on.update('kalavai.net', 'v1', 'kalavaijobs')
def update_fn(spec, name, status, namespace, patch, logger, **kwargs):
    """
    Delete old instance and replace it with a new one
    """
    delete(
        status=status,
        namespace=namespace,
        logger=logger
    )

    result = create(
        spec=spec,
        name=name,
        namespace=namespace,
        patch=patch,
        logger=logger
    )
    return result

@kopf.on.delete('kalavai.net', 'v1', 'kalavaijobs')
def delete_fn(status, namespace, logger, **kwargs):
    """
    Triggered when the object is marked for deletion.
    Kopf automatically handles the Finalizer logic here.

    Delete job with helm
    """
    delete(
        status=status,
        namespace=namespace,
        logger=logger
    )
    

# Only watch pods that have our specific label
# For CRDs, use format: @kopf.on.event('group', 'version', 'plural')
#   Then update rbac permissions
    # - apiGroups: ["ray.io"]
    #   resources: ["rayclusters"]
    #   verbs: ["get", "list", "watch"]
@kopf.on.event('pods', labels={'app': 'kv-worker'})
def watch_children(event, logger, **kwargs):
    """Continuously monitor status and update CRD"""
    pod = event['object']
    status = pod.get('status', {}).get('phase')
    name = pod['metadata']['name']
    
    # Check the event type (ADDED, MODIFIED, DELETED)
    event_type = event['type']
    
    if event_type == 'MODIFIED' and status == 'Running':
        logger.info(f"Child Pod {name} is now healthy and Running.")
    
    if status == 'Failed':
        logger.error(f"Child Pod {name} has failed! Check logs.")