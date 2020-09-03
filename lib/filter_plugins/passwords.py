#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright © 2ndQuadrant Limited <info@2ndquadrant.com>

import hmac
import base64
import hashlib
from passlib.hash import scram
from ansible.errors import AnsibleFilterError

# Returns ``'md5' || md5_hex( password || username ))`` as computed by
# pg_md5_encrypt() in src/common/md5.c

def md5_password(password, username):
    return 'md5%s' % hashlib.md5(password.encode('utf-8') + username.encode('utf-8')).hexdigest()

# Returns ``SCRAM-SHA-256$<iteration count>:<salt>$<StoredKey>:<ServerKey>`` as
# computed by scram_build_verifier() in src/common/scram-common.c

def scram_password(password):
    s = scram.using(rounds=4096, algs="sha-1,sha-256").hash(password)

    (salt, rounds, SaltedPassword) = scram.extract_digest_info(s, "sha-256")

    ClientKey = hmac.digest(SaltedPassword, "Client Key".encode('ascii'), hashlib.sha256)
    ServerKey = hmac.digest(SaltedPassword, "Server Key".encode('ascii'), hashlib.sha256)
    StoredKey = hashlib.sha256(ClientKey).digest()

    return '%s$%s:%s$%s:%s' % (
        'SCRAM-SHA-256', rounds,
        base64.b64encode(salt).decode('ascii'),
        base64.b64encode(StoredKey).decode('ascii'),
        base64.b64encode(ServerKey).decode('ascii')
    )

# Takes a password_encryption value, a password, and a username (required only
# if password_encryption == 'md5') and returns a string that is suitable for use
# as the PASSWORD in CREATE USER commands.

def encrypted_password(password_encryption, password, username=None):
    if password_encryption == 'scram-sha-256':
        return scram_password(password)
    elif password_encryption == 'md5':
        return md5_password(password, username)

    raise AnsibleFilterError("|encrypted_password does not recognise password_encryption scheme %s" % password_encryption)

class FilterModule(object):
    def filters(self):
        return {
            'encrypted_password': encrypted_password,
        }
