import os
import uuid

import kopf
from kubernetes import client, config


TEMPLATE_LABEL = os.getenv("TEMPLATE_LABEL", "kalavai.job.name")
HELM_PLURAL = "helmreleases"
HELM_API_VERSION = "v2"
HELM_GROUP = "helm.toolkit.fluxcd.io"
KALAVAI_PLURAL = "kalavaijobs"
KALAVAI_API_VERSION = "v1"
KALAVAI_GROUP = "kalavai.net"


def create(spec, name, namespace, patch, logger):
    # 1. Extract the list of key-value pairs from the spec
    values = spec.get('template', {}).get("values", {}) 
    chart = spec.get('template', {}).get("chart", None)
    version = spec.get('template', {}).get("version", None)
    repo = spec.get('template', {}).get("repo", "kalavai-templates")
    priority_class = spec.get('priorityClassName', None)
    node_selectors = spec.get('nodeSelectors', None)
    node_selectors_ops = spec.get('nodeSelectorsOps', "OR")
    
    if not values:
        logger.warning(f"KalavaiJob '{name}' created with empty template.values")

    logger.info(f"---> Deploying KalavaiJob '{name}' in namespace '{namespace}'")

    # inject job id to values
    job_id = str(uuid.uuid4())
    
    # inject system values
    if "system" in values:
        logger.info("---> 'System' property found in provided values. It will be overwritten")
    values["system"] = {
        "priorityClassName": priority_class,
        "nodeSelectors": node_selectors,
        "nodeSelectorsOps": node_selectors_ops,
        "jobId": job_id
    }
    # Deploy helm template chart
    helm_specs = {
        "chart": chart,
        "sourceRef": {
            "kind": "HelmRepository",
            "name": repo,
            "namespace": "default",
        }
    }
    if version is not None:
        helm_specs["version"] = version
    
    helm_release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": name,
            "labels": {
                TEMPLATE_LABEL: job_id
            }
        },
        "spec": {
            "interval": "10m",
            "chart": {
                "spec": helm_specs
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
    #patch.status['jobId'] = job_id
    # add job id to labels for quick search
    patch.metadata.labels["jobId"] = job_id
    logger.info(f"---> KalavaiJob created with id {job_id}")

    return {'status': 'synced', 'job_id': job_id}

def delete(body, namespace, logger):
    api = client.CustomObjectsApi()

    job_id = body.get("metadata", {}).get("labels", {}).get('jobId', None)

    if job_id is None:
        logger.warning(f"---> jobId not found, cannot delete")
        return
    
    label_selector = f"{TEMPLATE_LABEL}={job_id}"
    
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
            logger.info(f"---> Deleted KalavaiJob: {name}")

    except client.exceptions.ApiException as e:
        logger.warning(f"---> Exception when calling CustomObjectsApi: {e}")


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

@kopf.on.create(KALAVAI_GROUP, KALAVAI_API_VERSION, KALAVAI_PLURAL)
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

@kopf.on.field(KALAVAI_GROUP, KALAVAI_API_VERSION, KALAVAI_PLURAL, field='spec')
def update_fn(spec, name, body, namespace, patch, logger, **kwargs):
    """
    Delete old instance and replace it with a new one
    """
    logger.info(f"---> Spec for {name} changed! Re-creating resources...")
    delete(
        body=body,
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

@kopf.on.delete(KALAVAI_GROUP, KALAVAI_API_VERSION, KALAVAI_PLURAL)
def delete_fn(body, namespace, logger, **kwargs):
    """
    Triggered when the object is marked for deletion.
    Kopf automatically handles the Finalizer logic here.

    Delete job with helm
    """
    delete(
        body=body,
        namespace=namespace,
        logger=logger
    )

# watch status changes on the HELM release object created
@kopf.on.field(HELM_GROUP, HELM_API_VERSION, HELM_PLURAL, field='status.conditions')
def sync_all_helm_conditions(old, new, name, namespace, body, logger, **kwargs):
    """
    Replicates the entire conditions list from a HelmRelease to a parent CR.
    """
    if not new:
        return

    # 1. Transform the conditions into a format for your CR
    # We map them to ensure we only take the fields we want (cleanliness)
    captured_conditions = []
    for cond in new:
        captured_conditions.append({
            "type": cond.get('type'),
            "status": cond.get('status'),
            "reason": cond.get('reason'),
            "message": cond.get('message'),
            "lastTransitionTime": cond.get('lastTransitionTime')
        })

    # 2. Extract link to parent CR
    job_id = body.get('metadata', {}).get('labels', {}).get(TEMPLATE_LABEL)
    if not job_id:
        logger.warning(f"---> No job id found in helm release {name}")
        return

    custom_api = client.CustomObjectsApi()
    
    try:
        # 3. Find parent CRs
        parent_crs = custom_api.list_namespaced_custom_object(
            group=KALAVAI_GROUP, version=KALAVAI_API_VERSION, namespace=namespace,
            plural=KALAVAI_PLURAL, label_selector=f"jobId={job_id}"
        )
        if len(parent_crs) == 0:
            logger.warning(f"---> Job id {job_id} did not have a corresponding CR")
            return
        for cr in parent_crs.get('items', []):
            cr_name = cr['metadata']['name']
            # 4. Patch the CR status with the full list
            patch_body = {
                "status": {
                    "releases": {
                        "name": name,
                        "conditions": captured_conditions
                    }
                }
            }
            custom_api.patch_namespaced_custom_object_status(
                group=KALAVAI_GROUP, version=KALAVAI_API_VERSION, namespace=namespace,
                plural=KALAVAI_PLURAL, name=cr_name, body=patch_body
            )
            logger.info(f"---> Replicated {len(captured_conditions)} conditions to {cr_name}")

    except Exception as e:
        logger.error(f"---> Failed to replicate conditions: {e}")

# watch pods related to the job
@kopf.on.field('pods', field='status.phase', labels={TEMPLATE_LABEL: kopf.PRESENT})
def pod_status_change(old, new, name, namespace, body, logger, **kwargs):
    """
    Triggers only when a Pod with label 'monitored-by=my-operator' 
    changes its status.phase (e.g., Pending -> Running).
    """
    logger.info(f"---> Pod {namespace}/{name} changed status from {old} to {new}")
    job_id = body.get('metadata', {}).get('labels', {}).get(TEMPLATE_LABEL)
    node_name = body.get('spec', {}).get('nodeName', 'Unassigned')
    status = body.get('status', {})
    phase = status.get('phase')
    conditions = status.get('conditions', [])
    container_statuses = status.get('containerStatuses', [])
    # Calculate total restarts across all containers
    restart_count = sum(c.get('restartCount', 0) for c in container_statuses)

    logger.info(f"---> Pod {name} | Phase: {phase} | Restarts: {restart_count}")
    
    custom_api = client.CustomObjectsApi()

    # 2. Find the CRD instance that matches this jobId
    # We assume the CRD was also labeled with jobId during creation
    try:
        parent_crs = custom_api.list_namespaced_custom_object(
            group=KALAVAI_GROUP,
            version=KALAVAI_API_VERSION,
            namespace=namespace,
            plural=KALAVAI_PLURAL,
            label_selector=f"jobId={job_id}"
        )
        
        items = parent_crs.get('items', [])
        if not items:
            logger.warning(f"No CR found for jobId: {job_id}")
            return
            
        # Assuming 1:1 relationship between jobId and CR
        parent_cr = items[0]
        parent_name = parent_cr['metadata']['name']
        logger.info(f"---> KalavaiJob CR found: {namespace}/{parent_name}")

    except client.exceptions.ApiException as e:
        logger.error(f"---> Error searching for CR: {e}")
        return

    # 3. Update the specific CR status
    patch_body = {
        "status": {
            "pods": {
                name: {
                    "nodeName": node_name,
                    "phase": phase,
                    "restarts": restart_count,
                    # Optionally store the last few conditions for history
                    "conditions": conditions
                }
            }
        }
    }
    custom_api.patch_namespaced_custom_object_status(
        group=KALAVAI_GROUP,
        version=KALAVAI_API_VERSION,
        namespace=namespace,
        plural=KALAVAI_PLURAL,
        name=parent_name,
        body=patch_body
    )
    logger.info(f"---> Updated CR {namespace}/{parent_name} via jobId {job_id}")

# Watch services related to the job
@kopf.on.field('services', field='spec.ports', labels={TEMPLATE_LABEL: kopf.PRESENT})
def on_nodeport_assigned(old, new, meta, spec, logger, **_):
    """
    old:  The previous value of spec.ports
    new:  The current value of spec.ports
    spec: The ENTIRE spec dictionary of the Service
    meta: The metadata (names, labels)
    """
    job_id = meta.get('labels', {}).get(TEMPLATE_LABEL)
    
    svc_name = meta.get('name')
    namespace = meta.get('namespace')

    # 1. Extract interesting networking info
    # Get NodePorts if they exist
    #node_ports = {p.get('name'): p.get('nodePort') for p in spec.get('ports', []) if p.get('nodePort')}

    # 2. Find the Parent CR using the jobId label
    custom_api = client.CustomObjectsApi()
    try:
        parent_crs = custom_api.list_namespaced_custom_object(
            group=KALAVAI_GROUP,
            version=KALAVAI_API_VERSION,
            namespace=namespace,
            plural=KALAVAI_PLURAL,
            label_selector=f"jobId={job_id}"
        )
        
        if not parent_crs.get('items'):
            logger.info(f"---> Parent CR not found for jobId {job_id}")
            return
        parent_name = parent_crs['items'][0]['metadata']['name']
        logger.info(f"---> KalavaiJob CR found {namespace}/{parent_name}")
        # 3. Patch the ServiceRecords section of the CR
        patch_body = {
            "status": {
                "services": {
                    svc_name: {
                        "clusterIP": spec.get('clusterIP'),
                        "ports": spec.get('ports', [])
                    }
                }
            }
        }
        custom_api.patch_namespaced_custom_object_status(
            group=KALAVAI_GROUP, version=KALAVAI_API_VERSION, namespace=namespace,
            plural=KALAVAI_PLURAL, name=parent_name, body=patch_body
        )
        
    except client.exceptions.ApiException as e:
        logger.error(f"---> Failed to sync service {svc_name} to CR: {e}")
