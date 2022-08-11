#!/usr/bin/env python3
"""Upload model to Shapeways and get a quote for printing"""

import base64
import json
from pathlib import Path
from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.exceptutil import InvalidArgumentError
from pyapputil.logutil import GetLogger, logargs
from pyapputil.typeutil import ValidateAndDefault, StrType
import requests
import time

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "model_file": (StrType(), None),
    "client_id": (StrType(), None),
    "client_secret": (StrType(), None),
})
def upload_model(model_file,
                 client_id,
                 client_secret):
    log = GetLogger()

    model_file = Path(model_file)
    if not model_file.exists():
        raise InvalidArgumentError(f"File does not exist: {model_file}")

    # Log in to shapeways and get an access token
    log.info("Logging in to Shapeways...")
    api_url = "https://api.shapeways.com/oauth2/token"
    api_data = {"grant_type": "client_credentials"}
    with requests.post(url=api_url, data=api_data, auth=(client_id, client_secret)) as r:
        r.raise_for_status()
        resp = r.json()
    access_token = resp["access_token"]

    # Upload the model
    log.info("Reading model file...")
    with open(model_file, "rb") as fp:
        model_data = fp.read()

    log.info("Uploading model...")
    api_url = "https://api.shapeways.com/models/v1"
    api_data = json.dumps({
        "fileName": model_file.name,
        "file": base64.b64encode(model_data).decode("utf-8"), # base64 encode the data and convert to UTF8 string
        "hasRightsToModel": 1,
        "acceptTermsAndConditions": 1
    })
    api_headers = { "Authorization": f"Bearer {access_token}"}
    with requests.post(url=api_url, data=api_data, headers=api_headers) as r:
        log.debug(r.url)
        log.debug(api_data)
        r.raise_for_status()
        resp = r.json()
    model_id = resp["modelId"]

    # Get the list of materials
    log.info("Getting material list...")
    api_url = "https://api.shapeways.com/materials/v1"
    api_headers = { "Authorization": f"Bearer {access_token}"}
    with requests.get(api_url, headers=api_headers) as r:
        r.raise_for_status()
        resp = r.json()
    materials = {}
    for mat_id, mat in resp["materials"].items():
        if mat["title"] in ("Glossy Full Color Sandstone",
                            "Natural Full Color Sandstone",
                            "Matte High Definition Full Color",
                            "Standard High Definition Full Color",
                            "Smooth Full Color Nylon 12 (MJF)",
                            "Natural Full Color Nylon 12 (MJF)"):
            materials[mat_id] = mat

    # Wait for shapeways processing to complete
    log.info("Waiting for model processing and price quote...")
    api_url = f"https://api.shapeways.com/models/{model_id}/v1"
    api_headers = { "Authorization": f"Bearer {access_token}"}

    show_printable = True
    while True:
        with requests.get(api_url, headers=api_headers) as r:
            r.raise_for_status()
            resp = r.json()

        # Wait for printable
        if resp["printable"] == "processing":
            time.sleep(10)
            continue
        if resp["printable"] == "yes" and show_printable:
            log.passed("Model is printable")
            log.info(resp["urls"]["editModelUrl"]["address"])
            show_printable = False
        else:
            log.error(f"Model is not printable: {resp['printable']}")
            log.error(resp["urls"]["editModelUrl"]["address"])
            break

        # Wait for price quote
        prices = []
        for mat_id, mat in materials.items():
            if mat_id in resp["materials"]:
                if resp['materials'][mat_id]['price'] <= 0:
                    time.sleep(10)
                    continue
                prices.append(f"    {mat['title']} - ${resp['materials'][mat_id]['price']:.2f}")
        for line in prices:
            log.info(line)
        break


if __name__ == '__main__':
    parser = ArgumentParser(description="Upload model to Shapeways and get a quote for printing")
    parser.add_argument("-m", "--model-file", type=StrType(), metavar="FILENAME", help="Zipped model file to upload")
    parser.add_argument("-c", "--client-id", type=StrType(), metavar="ID", help="Shapeways API client ID")
    parser.add_argument("-s", "--client-secret", type=StrType(), metavar="SECRET", help="Shapeways API secret")

    args = parser.parse_args_to_dict()

    app = PythonApp(upload_model, args)
    app.Run(**args)
