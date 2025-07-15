from typing import List
from uuid import uuid4
from bson import ObjectId
from fastapi import  FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from db import generate_fhir_exercise_bundle, generate_fhir_patient_bundle, user_collection,patient_data_collection,test_data_collection, therapist_data_collection, devices
from datetime import datetime

from models import ExerciseRecord, LoginRequest, PatientData, Therapist, User

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"Message": "use '/docs' endpoint to find all the api related docs "}

@app.post("/login")
async def login(user: LoginRequest):
    # Determine which collection to use based on user type
    collection = user_collection if user.type == "patient" else therapist_data_collection

    # Retrieve user by email
    db_user = await collection.find_one({"email": user.email})
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Check password
    if db_user["password"] != user.password:
        raise HTTPException(status_code=401, detail="Incorrect password")

    return {
        "message": "Login successful",
        "username": db_user["username"],
        "type": db_user["type"]
    }


@app.post("/register/user")
async def register(user: User):
    # Check if the email is already registered
    existing_user = await user_collection.find_one({"email": user.email})
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Store user data in MongoDB with plain text password
    await user_collection.insert_one({
        "username": user.username,
        "email": user.email,
        "password": user.password,  # Store plain text password
        "type": user.type  # Added type field
    })
    
    return {"message": "User registered successfully"}

@app.post("/register/therapist")
async def register_therapist(therapist: Therapist):
    # Check if the email is already registered
    existing_therapist = await therapist_data_collection.find_one({"email": therapist.email})
    
    if existing_therapist:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Insert the entire object as a dictionary
    await therapist_data_collection.insert_one(therapist.dict())
    
    return {"message": "Therapist registered successfully"}


@app.post("/patient-data")
async def post_patient_data(patient_data: PatientData):
    # Check if patient already exists by email
    existing_patient = await patient_data_collection.find_one({
        "entry": {
            "$elemMatch": {
                "resource.resourceType": "Observation",
                "resource.code.text": "Email",
                "resource.valueString": patient_data.email
            }
        }
    })

    if existing_patient:
        raise HTTPException(status_code=400, detail="Email already registered with a patient")

    # Generate FHIR bundle
    fhir_bundle = generate_fhir_patient_bundle(patient_data)

    # Insert into DB
    try:
        result = await patient_data_collection.insert_one(fhir_bundle)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database insert failed: {str(e)}")

    return {
        "message": "Patient data successfully added in FHIR format",
        "patient_id": str(result.inserted_id)
    }

@app.get("/fhir/export/{therapist_email}")
async def export_bundles(therapist_email: str):
    cursor = patient_data_collection.find({
        "entry.resource.resourceType": "Observation",
        "entry.resource.code.text": "Therapist Assigned",
        "entry.resource.valueString": therapist_email
    })
    
    bundles = await cursor.to_list(length=None)

    # Convert ObjectId to string
    for bundle in bundles:
        if "_id" in bundle:
            bundle["_id"] = str(bundle["_id"])

    return JSONResponse(content=bundles, media_type="application/fhir+json")


@app.get("/getTherapist/{email}", response_model=Therapist)
async def get_therapist_by_email(email: str):
    therapist = await therapist_data_collection.find_one({"email": email, "type": "therapist"})

    if not therapist:
        raise HTTPException(status_code=404, detail="Therapist not found")

    # Convert MongoDB ObjectId to string (optional, only if needed for serialization)
    therapist["_id"] = str(therapist["_id"]) if "_id" in therapist else None

    return Therapist(**therapist)

@app.get("/fhir/export/patient/{email}")
async def export_patient_bundle(email: str):
    # Query to match Observation with code "Email" and valueString = email
    bundle = await patient_data_collection.find_one({
        "entry.resource.code.text": "Email",
        "entry.resource.valueString": email
    })

    if not bundle:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Convert ObjectId to string to avoid JSON serialization error
    if "_id" in bundle:
        bundle["_id"] = str(bundle["_id"])

    return JSONResponse(content=bundle, media_type="application/fhir+json")

@app.post("/upload-exercise/")
async def upload_exercise(email: str, first_name: str, last_name: str, exerciseRecord: List[ExerciseRecord]):
    # Step 1: Look up the patient in `patient_data_collection`
    patient_record = await patient_data_collection.find_one({
    "$and": [
        {
            "entry": {
                "$elemMatch": {
                    "resource.resourceType": "Observation",
                    "resource.code.text": "Email",
                    "resource.valueString": email
                }
            }
        },
        {
            "entry": {
                "$elemMatch": {
                    "resource.resourceType": "Patient",
                    "resource.name.0.given.0": first_name,
                    "resource.name.0.family": last_name
                }
            }
        }
    ]
})


    if not patient_record:
        raise HTTPException(status_code=404, detail="Patient not found in patient_data_collection")

    # Step 2: Extract user_id and patient UUID
    user_id = None
    patient_uuid = None

    for entry in patient_record["entry"]:
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Observation" and resource.get("code", {}).get("text") == "User Id":
            user_id = resource.get("valueString")
        if resource.get("resourceType") == "Patient":
            patient_uuid = resource.get("id")

    if not user_id or not patient_uuid:
        raise HTTPException(status_code=500, detail="User ID or Patient ID not found in patient record")

    # Step 3: Check if there's an existing exercise bundle in `test_data_collection` for this user
    exercise_bundle = await test_data_collection.find_one({
        "entry": {
            "$elemMatch": {
                "resource.resourceType": "Observation",
                "resource.code.text": "User Id",
                "resource.valueString": user_id
            }
        }
    })

    # Step 4: Generate new exercise observations only (no patient or user_id entry)
    new_exercise_bundle = generate_fhir_exercise_bundle(
        user_id=user_id,
        patient_uuid=patient_uuid,
        exercise_records=[record.dict() for record in exerciseRecord],
        include_patient=False  # ⚠️ New flag, defined below
    )

    if exercise_bundle:
        # ✅ Append new exercise observations to existing document
        new_observations = new_exercise_bundle["entry"]
        await test_data_collection.update_one(
            {"_id": exercise_bundle["_id"]},
            {"$push": {"entry": {"$each": new_observations}}}
        )
        return {
            "message": "Exercise data added to existing test_data_collection bundle",
            "user_id": user_id
        }

    else:
        # ❌ No previous exercise bundle, create new one (include patient + user ID)
        full_bundle = generate_fhir_exercise_bundle(
            user_id=user_id,
            patient_uuid=patient_uuid,
            exercise_records=[record.dict() for record in exerciseRecord],
            include_patient=True
        )

        result = await test_data_collection.insert_one(full_bundle)
        return {
            "message": "New exercise bundle created in test_data_collection",
            "user_id": user_id,
            "bundle_id": str(result.inserted_id)
        }
    
@app.get("/get-exercise-bundles/{user_id}")
async def get_exercise_bundles(user_id: str):
    # Find all documents in test_data_collection with matching user_id Observation
    cursor = test_data_collection.find({
        "entry": {
            "$elemMatch": {
                "resource.resourceType": "Observation",
                "resource.code.text": "User Id",
                "resource.valueString": user_id
            }
        }
    })

    bundles = await cursor.to_list(length=None)

    # Convert ObjectId to string for JSON serialization
    for bundle in bundles:
        if "_id" in bundle:
            bundle["_id"] = str(bundle["_id"])

    if not bundles:
        raise HTTPException(status_code=404, detail="No exercise bundles found for this user ID")

    return JSONResponse(content=bundles, media_type="application/fhir+json")

@app.get("/activate")
async def activate_device(
    device_id: str,
    token: str,
    company_name: str,
    location_scanned: str,
    therapist_email: str
):
    # Step 1: Find device
    device = await devices.find_one({"device_id": device_id, "token": token})
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found or token mismatch")

    # Step 2: Check if already activated
    if "license_activated" in device:
        return {
            "message": "Device already activated",
            "device_id": device_id,
            "company": device.get("company_name"),
            "location": device.get("location_scanned"),
            "therapist_email": device.get("therapist_email"),
            "activated_at": device.get("license_activated")
        }

    # Step 3: First-time activation
    update_data = {
        "company_name": company_name,
        "location_scanned": location_scanned,
        "therapist_email": therapist_email,
        "license_activated": datetime.utcnow()
    }

    await devices.update_one(
        {"_id": device["_id"]},
        {"$set": update_data}
    )

    return {
        "message": "Device activated successfully",
        "device_id": device_id,
        "company": company_name,
        "location": location_scanned,
        "therapist_email": therapist_email
    }