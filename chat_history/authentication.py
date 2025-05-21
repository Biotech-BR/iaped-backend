import os
import firebase_admin
from firebase_admin import credentials, auth as fb_auth
from django.contrib.auth import get_user_model
from rest_framework import authentication, exceptions

cred = credentials.Certificate(os.getenv("FIREBASE_ADMIN_CREDENTIALS"))
firebase_admin.initialize_app(cred)

User = get_user_model()

class FirebaseAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header.split(" ",1)[1]
        try:
            decoded = fb_auth.verify_id_token(token)
        except:
            raise exceptions.AuthenticationFailed("Token inválido ou expirado")
        uid = decoded.get("uid")
        user, _ = User.objects.get_or_create(username=uid)
        return (user, None)
