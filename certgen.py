import datetime, os, sys

cert, key = "cert.pem", "key.pem"
if os.path.exists(cert) and os.path.exists(key):
    sys.exit(0)

try:
    import subprocess
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", key, "-out", cert, "-days", "3650", "-nodes",
        "-subj", "/CN=localhost"], check=True, capture_output=True)
    print("Certificate generated via openssl")
    sys.exit(0)
except:
    pass

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    
    k = rsa.generate_private_key(65537, 2048)
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    c = (x509.CertificateBuilder()
        .subject_name(n).issuer_name(n)
        .public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(k, hashes.SHA256()))
    with open(key, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with open(cert, "wb") as f:
        f.write(c.public_bytes(serialization.Encoding.PEM))
    print("Certificate generated via Python")
except ImportError:
    print("WARNING: openssl not found, skipping certificate generation")
    print("  The server will fall back to HTTP (microphone unavailable)")
    print("  Install openssl or 'pip install cryptography' to generate a certificate")
    sys.exit(1)
