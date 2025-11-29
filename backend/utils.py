from typing import Any, Dict, List, Union
from bson import ObjectId


def convert_objectid(doc: Union[Dict, List, Any]) -> Union[Dict, List, Any]:
    if doc is None:
        return doc

    if isinstance(doc, ObjectId):
        return str(doc)

    if isinstance(doc, list):
        return [convert_objectid(item) for item in doc]

    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if isinstance(value, ObjectId):
                result[key] = str(value)
            elif isinstance(value, dict):
                result[key] = convert_objectid(value)
            elif isinstance(value, list):
                result[key] = convert_objectid(value)
            else:
                result[key] = value

        if "_id" in result:
            result["patient_id"] = result["_id"]

        return result

    return doc
