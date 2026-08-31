# API summary

POST /api/auth/login
GET  /api/dashboard
GET  /api/banks
POST /api/banks
GET  /api/vehicles/search?field=registration_no&q=ABC
POST /api/vehicles/<id>/status
POST /api/files/import
GET  /api/users
GET  /api/assignments
POST /api/assignments
GET  /api/notifications
GET  /api/sync/download?after_version=0&limit=500
POST /api/sync/mutations

Authenticated endpoints require:
Authorization: Bearer <JWT>
