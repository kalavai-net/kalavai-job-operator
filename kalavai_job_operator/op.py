import kopf
import requests # For the actual API call later

@kopf.on.create('kalavai.net', 'v1', 'kalavaijobs')
def create_fn(spec, name, namespace, logger, **kwargs):
    """
    Triggered when a new KalavaiJob object is created.
    """
    # 1. Extract the list of key-value pairs from the spec
    kv_pairs = spec.get('data', [])
    
    if not kv_pairs:
        logger.info(f"KalavaiJob '{name}' created with no data.")
        return

    logger.info(f"Processing {len(kv_pairs)} items for KalavaiJob '{name}'")

    # 2. Iterate and "Call the External API"
    for item in kv_pairs:
        k = item.get('key')
        v = item.get('value')
        
        # This is where your external API call would live
        # Example: requests.post("https://api.external.com/sync", json={"key": k, "val": v})
        logger.info(f"SYNCING TO API -> Key: {k}, Value: {v}")

    # 3. Optional: Return data to store in the object's status
    return {'status': 'synced', 'items_processed': len(kv_pairs)}