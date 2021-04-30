import base64
import datetime
import json
import uuid
import logging
import config

from flask import Flask, render_template, request, redirect
import requests
import urllib.parse

app = Flask(__name__)
app.config.from_pyfile('config.py')
app.config.from_pyfile('./instance/config.py')

SERVER_HOST=app.config.get('SERVER_HOST', 'localhost')
SERVER_PORT=app.config.get('SERVER_PORT', 9090)
SERVER_URL=f'http://{SERVER_HOST}:{SERVER_PORT}'

settings = {
    'apiUrl': 'https://sandboxapi.deere.com/platform',
    'clientId': app.config.get('CLIENT_ID', ''),
    'clientSecret': app.config.get('CLIENT_SECRET', ''),
    'wellKnown': 'https://signin.johndeere.com/oauth2/aus78tnlaysMraFhC1t7/.well-known/oauth-authorization-server',
    'callbackUrl': f"{SERVER_URL}/callback",
    'orgConnectionCompletedUrl': SERVER_URL,
    'scopes': 'ag1 ag2 ag3 eq1 eq2 org1 org2 files offline_access',
    'state': uuid.uuid1(),
    'idToken': '',
    'accessToken': '',
    'refreshToken': '',
    'apiResponse': '',
    'accessTokenDetails': '',
    'exp': ''
}


def populate(data):
    settings['clientId'] = data['clientId']
    settings['clientSecret'] = data['clientSecret']
    settings['wellKnown'] = data['wellKnown']
    settings['callbackUrl'] = data['callbackUrl']
    settings['scopes'] = data['scopes']
    settings['state'] = data['state']


def update_token_info(res):
    json_response = res.json()
    token = json_response['access_token']
    settings['accessToken'] = token
    settings['refreshToken'] = json_response['refresh_token']
    settings['exp'] = datetime.datetime.now() + datetime.timedelta(seconds=json_response['expires_in'])
    (header, payload, sig) = token.split('.')
    payload += '=' * (-len(payload) % 4)
    settings['accessTokenDetails'] = json.dumps(json.loads(base64.urlsafe_b64decode(payload).decode()), indent=4)


def get_location_from_metadata(endpoint):
    response = requests.get(settings['wellKnown'])
    return response.json()[endpoint]


def get_basic_auth_header():
    return base64.b64encode(bytes(settings['clientId'] + ':' + settings['clientSecret'], 'utf-8'))

def api_get(access_token, resource_url):
    headers = {
        'authorization': 'Bearer ' + settings['accessToken'],
        'Accept': 'application/vnd.deere.axiom.v3+json'
    }
    return requests.get(resource_url, headers=headers)

def render_error(message):
    return render_template('error.html', title='John Deere API with Python', error=message)


def get_oidc_query_string():
    query_params = {
        "client_id": settings['clientId'],
        "response_type": "code",
        "scope": urllib.parse.quote(settings['scopes']),
        "redirect_uri": settings['callbackUrl'],
        "state": settings['state'],
    }
    params = [f"{key}={value}" for key, value in query_params.items()]
    return "&".join(params)


@app.route("/", methods=['POST'])
def start_oidc():
    populate(request.form)
    redirect_url = f"{get_location_from_metadata('authorization_endpoint')}?{get_oidc_query_string()}"

    return redirect(redirect_url, code=302)

def needs_organization_access():
    """Check if a another redirect is needed to finish the connection.

    Check to see if the 'connections' rel is present for any organization.
    If the rel is present it means the oauth application has not completed its
    access to an organization and must redirect the user to the uri provided
    in the link.
    """
    api_response = api_get(settings['accessToken'], settings['apiUrl']+'/organizations').json()
    print(json.dumps(api_response, indent=True))
    for org in api_response['values']:
        for link in org['links']:
            if link['rel'] == 'connections':
                connectionsUri = link['uri']
                query = urllib.parse.urlencode({'redirect_uri': settings['orgConnectionCompletedUrl']})
                return f"{connectionsUri}?{query}"
    return None

@app.route("/callback")
def process_callback():
    try:
        code = request.args['code']
        headers = {
            'authorization': 'Basic ' + get_basic_auth_header().decode('utf-8'),
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        payload = {
            'grant_type': 'authorization_code',
            'redirect_uri': settings['callbackUrl'],
            'code': code,
            'scope': settings['scopes']
        }

        res = requests.post(get_location_from_metadata('token_endpoint'), data=payload, headers=headers)
        update_token_info(res)

        organization_access_url = needs_organization_access()
        if organization_access_url is not None:
            return redirect(organization_access_url, code=302)


        return index()
    except Exception as e:
        logging.exception(e)
        return render_error('Error getting token!')


@app.route("/call-api", methods=['POST'])
def call_the_api():
    try:
        url = request.form['url']
        res = api_get(settings['accessToken'], url)
        settings['apiResponse'] = json.dumps(res.json(), indent=4)
        return index()
    except Exception as e:
        logging.exception(e)
        return render_error('Error calling API!')


@app.route("/refresh-access-token")
def refresh_access_token():
    try:
        headers = {
            'authorization': 'Basic ' + get_basic_auth_header().decode('utf-8'),
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        payload = {
            'grant_type': 'refresh_token',
            'redirect_uri': settings['callbackUrl'],
            'refresh_token': settings['refreshToken'],
            'scope': settings['scopes']
        }

        res = requests.post(get_location_from_metadata('token_endpoint'), data=payload, headers=headers)
        update_token_info(res)
        return index()
    except Exception as e:
        logging.exception(e)
        return render_error('Error getting refresh token!')


@app.route("/")
def index():
    return render_template('main.html', title='John Deere API with Python', settings=settings)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=SERVER_PORT)
