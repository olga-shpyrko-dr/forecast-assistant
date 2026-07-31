#!/usr/bin/env bash

# React frontend is prebuilt into forecastic/build and served by FastAPI
# (forecastic/rest_api.py mounts it as static files). DataRobot custom apps
# must listen on port 8080.
uvicorn forecastic.rest_api:app --host 0.0.0.0 --port 8080
