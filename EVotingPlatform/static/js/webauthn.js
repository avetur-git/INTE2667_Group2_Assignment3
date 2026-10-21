// REQ-10: browser side of WebAuthn. The server sends options with binary
// fields as base64url strings; the browser API needs ArrayBuffers, and the
// response has to go back as base64url JSON.

function b64urlToBuffer(value) {
    const b64 = value.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - b64.length % 4) % 4);
    return Uint8Array.from(atob(padded), c => c.charCodeAt(0)).buffer;
}

function bufferToB64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    bytes.forEach(b => { binary += String.fromCharCode(b); });
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function requireWebAuthn() {
    if (!window.PublicKeyCredential) {
        throw new Error('This browser does not support security keys (WebAuthn).');
    }
}

async function createCredential(options) {
    requireWebAuthn();
    const publicKey = {
        ...options,
        challenge: b64urlToBuffer(options.challenge),
        user: { ...options.user, id: b64urlToBuffer(options.user.id) },
        excludeCredentials: (options.excludeCredentials || []).map(c => ({ ...c, id: b64urlToBuffer(c.id) })),
    };
    const cred = await navigator.credentials.create({ publicKey });
    return {
        id: cred.id,
        rawId: bufferToB64url(cred.rawId),
        type: cred.type,
        response: {
            clientDataJSON: bufferToB64url(cred.response.clientDataJSON),
            attestationObject: bufferToB64url(cred.response.attestationObject),
            transports: cred.response.getTransports ? cred.response.getTransports() : [],
        },
        clientExtensionResults: cred.getClientExtensionResults(),
        authenticatorAttachment: cred.authenticatorAttachment,
    };
}

async function getAssertion(options) {
    requireWebAuthn();
    const publicKey = {
        ...options,
        challenge: b64urlToBuffer(options.challenge),
        allowCredentials: (options.allowCredentials || []).map(c => ({ ...c, id: b64urlToBuffer(c.id) })),
    };
    const cred = await navigator.credentials.get({ publicKey });
    return {
        id: cred.id,
        rawId: bufferToB64url(cred.rawId),
        type: cred.type,
        response: {
            clientDataJSON: bufferToB64url(cred.response.clientDataJSON),
            authenticatorData: bufferToB64url(cred.response.authenticatorData),
            signature: bufferToB64url(cred.response.signature),
            userHandle: cred.response.userHandle ? bufferToB64url(cred.response.userHandle) : null,
        },
        clientExtensionResults: cred.getClientExtensionResults(),
        authenticatorAttachment: cred.authenticatorAttachment,
    };
}
