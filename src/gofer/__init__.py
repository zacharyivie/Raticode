import os

# Related-meaning search loads ONNX Runtime and downloads its model from Hugging Face.
# Both libraries read these switches once, when they first load, so set them before
# any import can reach them. Inherited values are overwritten: Raticode never sends
# their usage telemetry, whatever the surrounding environment says.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
