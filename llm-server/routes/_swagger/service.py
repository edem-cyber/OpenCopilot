import json
from qdrant_client import QdrantClient
import yaml
from typing import Dict, List
from flask import Request
from prance import ResolvingParser
from shared.utils.opencopilot_utils.get_vector_store import get_vector_store
from shared.utils.opencopilot_utils import StoreOptions
from langchain.docstore.document import Document
from utils.get_logger import CustomLogger
import os
from utils.db import Database

client = QdrantClient(url=os.getenv("QDRANT_URL", "http://qdrant:6333"))

db_instance = Database()
mongo = db_instance.get_db()

logger = CustomLogger(module_name=__name__)


def save_swaggerfile_to_mongo(
    filename: str, bot_id: str, swagger_doc: ResolvingParser
) -> bool:
    spec = swagger_doc.specification
    spec["meta"] = {"bot_id": bot_id, "swagger_url": filename}

    result = mongo.swagger_files.insert_one(spec)

    return result.acknowledged


def save_swagger_paths_to_qdrant(swagger_doc: ResolvingParser, bot_id: str):
    vector_store = get_vector_store(StoreOptions("apis"))
    try:
        # delete documents with metadata in api with the current bot id, before reingesting
        documents: List[Document] = []
        paths = swagger_doc.specification.get("paths", {})
        
        for path, operations in paths.items():
            for method, operation in operations.items():
                try:
                    operation["method"] = method
                    operation["path"] = path
                    del operation["responses"]
                    
                    # Check if "summary" key is present before accessing it
                    summary = operation.get('summary', '')
                    description = operation.get('description', '')
                    
                    document = Document(
                        page_content=f"{summary}; {description}"
                    )
                    document.metadata["bot_id"] = bot_id
                    document.metadata["operation"] = operation

                    logger.info(
                        "document before ingestion ---",
                        incident="ingestion_doc",
                        data=document.page_content,
                    )
                    documents.append(document)
                except KeyError as e:
                    # Handle the specific key error, log, or take necessary action
                    logger.error(f"KeyError in processing document: {e}")

        point_ids = vector_store.add_documents(documents)
        logger.info(
            "API ingestion for Qdrant",
            incident="api_ingestion_qdrant",
            point_ids=point_ids,
        )
    except KeyError as e:
        # Handle the specific key error at a higher level if needed
        logger.error(f"KeyError in processing paths: {e}")
    except Exception as e:
        # Handle other exceptions
        logger.error(f"An error occurred: {e}")

def add_swagger_file(request: Request, id: str) -> Dict[str, str]:
    if request.content_type == "application/json":
        # JSON file
        file_content = request.get_json()

    elif "multipart/form-data" in request.content_type:
        # Uploaded file
        file = request.files.get("file")
        if file is None:
            return {"error": "File upload is required"}

        if file.filename and file.filename.endswith(".json"):
            try:
                file_content = json.load(file)
            except json.JSONDecodeError as e:
                return {"error": "Invalid JSON format in uploaded file"}

        elif file.filename and (
            file.filename.endswith(".yaml") or file.filename.endswith(".yml")
        ):
            try:
                file_content = yaml.safe_load(file)
            except yaml.YAMLError as e:
                return {"error": "Invalid YAML format in uploaded file"}

    else:
        return {"error": "Unsupported content type"}

    inserted_id = mongo.swagger_files.insert_one(file_content).inserted_id

    return {"message": "File added successfully", id: str(inserted_id)}
