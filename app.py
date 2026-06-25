from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, session
import requests
import json
import os
from dotenv import load_dotenv
import base64
from flask_wtf.csrf import CSRFProtect
import markdown
import servers
from ollama_client import OllamaClient

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
csrf = CSRFProtect(app)

# OLLAMA_API_BASE is now only the seed for the first server (see servers.py).


def client_for(server_id):
    rec = servers.get_server(server_id)
    return OllamaClient(rec["base_url"]) if rec else None


def active_client():
    c = client_for(servers.get_active_id(session))
    if c is None:
        raise RuntimeError("No active server configured. Add or enable a server on the Servers page.")
    return c


def coerce_param(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def build_create_payload(data, stream):
    payload = {"model": data.get("model_name"), "stream": stream}
    if data.get("system_prompt"):
        payload["system"] = data["system_prompt"]
    if data.get("template"):
        payload["template"] = data["template"]
    if data.get("from_model"):
        payload["from"] = data["from_model"]
    if data.get("quantize"):
        payload["quantize"] = data["quantize"]
    params = {}
    if data.get("num_ctx"):
        params["num_ctx"] = int(data["num_ctx"])
    for row in data.get("parameters", []):
        key = (row.get("key") or "").strip()
        if key:
            params[key] = coerce_param(row.get("value"))
    if params:
        payload["parameters"] = params
    return payload

# App configuration
PORT = int(os.getenv('PORT', 5000))
HOST = os.getenv('HOST', '127.0.0.1')

@app.route('/')
def index():
    current_version = "Unknown"
    # Get current version from Ollama API
    try:
        client = active_client()
        version_data = client.version()
        current_version = version_data.get('version', 'Unknown')
    except Exception:
        pass
    return render_template('index.html', version=current_version)

def merged_models(enabled):
    """Query all enabled servers, merge models by name, detect drift."""
    merged = {}
    server_status = {}
    for s in enabled:
        try:
            data = OllamaClient(s["base_url"]).tags()
            server_status[s["id"]] = True
            for m in data.get("models", []):
                entry = merged.setdefault(m["name"], {
                    "name": m["name"], "size": m.get("size", 0),
                    "details": m.get("details", {}), "modified_at": m.get("modified_at"),
                    "present_on": [],
                })
                entry["present_on"].append(s["id"])
        except Exception:
            server_status[s["id"]] = False
    online_ids = {sid for sid, ok in server_status.items() if ok}
    models_list = list(merged.values())
    for m in models_list:
        m["missing_on"] = [s for s in enabled
                           if s["id"] in online_ids and s["id"] not in m["present_on"]]
        m["is_drift"] = len(m["missing_on"]) > 0
    return models_list, server_status


def _compute_modified_ago(modified_at):
    """Return a human-readable string for how long ago a model was modified."""
    from datetime import datetime
    if not modified_at:
        return 'Unknown'
    try:
        modified_time = datetime.fromisoformat(modified_at.replace('Z', '+00:00'))
        now = datetime.now().astimezone()
        time_diff = (now - modified_time).total_seconds()
        if time_diff < 60:
            return f"{int(time_diff)} seconds ago"
        elif time_diff < 3600:
            return f"{int(time_diff // 60)} minutes ago"
        elif time_diff < 86400:
            return f"{int(time_diff // 3600)} hours ago"
        elif time_diff < 604800:
            return f"{int(time_diff // 86400)} days ago"
        elif time_diff < 2592000:
            return f"{int(time_diff // 604800)} weeks ago"
        else:
            return f"{int(time_diff // 2592000)} months ago"
    except Exception:
        return 'Unknown'


@app.route('/models')
def models():
    enabled = servers.get_enabled()
    models_list, server_status = merged_models(enabled)
    sort_by = request.args.get('sort', 'name')
    sort_order = request.args.get('order', 'asc')
    rev = sort_order == 'desc'
    if sort_by == 'name':
        models_list.sort(key=lambda x: x['name'].lower(), reverse=rev)
    elif sort_by == 'size':
        models_list.sort(key=lambda x: x.get('size', 0), reverse=rev)
    elif sort_by == 'modified':
        models_list.sort(key=lambda x: x.get('modified_at') or '', reverse=rev)
    # Compute modified_ago for each model (preserved from original route)
    for m in models_list:
        m['modified_ago'] = _compute_modified_ago(m.get('modified_at'))
    drift_count = sum(1 for m in models_list if m['is_drift'])
    if not enabled:
        flash("No active server configured. Add or enable a server on the Servers page.", "danger")
    return render_template('models.html', models=models_list, servers=enabled,
                           server_status=server_status, drift_count=drift_count,
                           sort_by=sort_by, sort_order=sort_order)

@app.route('/models/<path:model_name>')
def model_detail(model_name):
    try:
        model_info = active_client().show(model_name)
        return render_template('model_detail.html', model=model_info, model_name=model_name)
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")
        return redirect(url_for('models'))

def broadcast_delete(model_name, target_ids):
    results = []
    for sid in target_ids:
        server = servers.get_server(sid)
        if not server:
            continue
        try:
            resp = OllamaClient(server["base_url"]).delete(model_name)
            ok = resp.status_code == 200
            results.append({"name": server["name"], "ok": ok,
                            "message": "" if ok else f"HTTP {resp.status_code}"})
        except Exception as e:
            results.append({"name": server["name"], "ok": False, "message": str(e)})
    return results


@app.route('/models/delete/<path:model_name>', methods=['POST'])
def delete_model(model_name):
    target_ids = request.form.getlist('target_ids')
    if not target_ids:
        target_ids = [s["id"] for s in servers.get_enabled()]
    results = broadcast_delete(model_name, target_ids)
    if not results:
        flash("No servers selected for deletion", "warning")
    for r in results:
        if r["ok"]:
            flash(f"Deleted {model_name} from {r['name']}", "success")
        else:
            flash(f"Failed to delete {model_name} from {r['name']}: {r['message']}", "danger")
    return redirect(url_for('models'))

@app.route('/models/update/<path:model_name>')
def update_model(model_name):
    try:
        # Re-pull the model to get the latest version
        response = active_client().pull(model_name, stream=False)
        if response.status_code == 200:
            flash(f"Model {model_name} updated successfully", "success")
        else:
            flash(f"Error updating model: {response.status_code}", "danger")
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")
    return redirect(url_for('models'))

@app.route('/pull')
def pull_page():
    return render_template('pull_model.html', servers=servers.get_enabled(),
                           prefill_model=request.args.get('model', ''),
                           prefill_targets=request.args.get('targets', '').split(','))


@app.route('/pull/stream', methods=['POST'])
def pull_stream():
    data = request.get_json(silent=True) or {}
    server_id = data.get('server_id')
    model = data.get('model')
    server = servers.get_server(server_id)
    if not server or not model:
        return jsonify({"error": "server_id and model are required"}), 400

    def generate():
        try:
            resp = OllamaClient(server["base_url"]).pull(model, stream=True)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'error': f'HTTP {resp.status_code}'})}\n\n"
                return
            for line in resp.iter_lines():
                if line:
                    yield f"data: {line.decode('utf-8')}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/create-model/stream', methods=['POST'])
def create_model_stream():
    data = request.get_json(silent=True) or {}
    server = servers.get_server(data.get('server_id'))
    if not server or not data.get('model_name'):
        return jsonify({"error": "server_id and model_name are required"}), 400
    try:
        payload = build_create_payload(data, stream=True)
    except (ValueError, TypeError) as e:
        return jsonify({"error": "Invalid parameter: " + str(e)}), 400

    def generate():
        try:
            resp = OllamaClient(server["base_url"]).create(payload, stream=True)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'error': f'HTTP {resp.status_code}'})}\n\n"
                return
            for line in resp.iter_lines():
                if line:
                    yield f"data: {line.decode('utf-8')}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@app.route('/create', methods=['GET'])
def create_model_page():
    try:
        models_data = active_client().tags()
        return render_template('create_model.html', models=models_data.get('models', []),
                               servers=servers.get_enabled())
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")
        return render_template('create_model.html', models=[], servers=servers.get_enabled())

@app.route('/create-model', methods=['GET', 'POST'])
def create_model():
    # For GET requests, redirect to the create model page
    if request.method == 'GET':
        return redirect(url_for('create_model_page'))
    
    # Handle POST requests
    # Handle both form data and JSON data
    if request.content_type and 'application/json' in request.content_type:
        # JSON request (from streaming)
        data = request.get_json()
        model_name = data.get('model_name') if data else None
        creation_method = data.get('creation_method') if data else None
        system_prompt = data.get('system_prompt') if data else None
        template = data.get('template') if data else None
        stream = data.get('stream') == 'on' if data else False
        from_model = data.get('from_model') if data else None
        quantize = data.get('quantize') if data else None
    else:
        # Form data request (non-streaming)
        model_name = request.form.get('model_name')
        creation_method = request.form.get('creation_method')
        system_prompt = request.form.get('system_prompt')
        template = request.form.get('template')
        stream = 'stream' in request.form
        from_model = request.form.get('from_model')
        quantize = request.form.get('quantize')
    
    if not model_name:
        flash("Model name is required", "danger")
        return redirect(url_for('create_model_page'))

    if not creation_method:
        flash("Creation method is required", "danger")
        return redirect(url_for('create_model_page'))

    # Build data dict for build_create_payload
    data = {
        "model_name": model_name,
        "system_prompt": system_prompt,
        "template": template,
        "from_model": from_model,
        "quantize": quantize,
    }

    # Handle creation method
    if creation_method == 'from_model':
        if not from_model:
            flash("Base model is required when creating from an existing model", "danger")
            return redirect(url_for('create_model_page'))

    # Handle file-based creation (placeholder for future implementation)
    elif creation_method == 'from_files':
        flash("Creating models from files is not yet implemented in the web interface", "warning")
        return redirect(url_for('create_model_page'))

    payload = build_create_payload(data, stream)

    try:
        if stream:
            return Response(stream_create_model(payload), mimetype='text/event-stream')
        else:
            # Call Ollama API to create the model (non-streaming)
            response = active_client().create(payload, stream=False)

            if response.status_code == 200:
                flash(f"Model {model_name} created successfully", "success")
            else:
                flash(f"Error creating model: {response.status_code} - {response.text}", "danger")

            return redirect(url_for('models'))
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")
        return redirect(url_for('create_model_page'))

def stream_create_model(payload):
    """Stream the model creation process from Ollama API."""
    try:
        # Make streaming request to Ollama API
        response = active_client().create(payload, stream=True)

        if response.status_code != 200:
            error_msg = f"Error from Ollama API: {response.status_code}"
            if hasattr(response, 'text'):
                error_msg += f" - {response.text}"
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
            return

        for line in response.iter_lines():
            if line:
                try:
                    # Forward the API response directly to the client
                    yield f"data: {line.decode('utf-8')}\n\n"
                except Exception:
                    continue

        # Send a final success message
        yield f"data: {json.dumps({'done': True, 'message': 'Model created successfully'})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

@app.route('/running-models')
def running_models():
    from datetime import datetime
    rows = []
    for s in servers.get_enabled():
        try:
            data = OllamaClient(s["base_url"]).ps()
            for model in data.get("models", []):
                model["server_name"] = s["name"]
                # Add expires_in calculation
                if model.get('expires_at'):
                    if model['expires_at'].startswith('0001-01-01'):
                        model['expires_in'] = 'Never'
                    else:
                        try:
                            expiry_time = datetime.fromisoformat(model['expires_at'].replace('Z', '+00:00'))
                            now = datetime.now().astimezone()
                            time_diff = (expiry_time - now).total_seconds()
                            if time_diff <= 0:
                                model['expires_in'] = 'Expired'
                            elif time_diff < 60:
                                model['expires_in'] = f"{int(time_diff)} seconds"
                            elif time_diff < 3600:
                                model['expires_in'] = f"{int(time_diff // 60)} minutes"
                            elif time_diff < 86400:
                                model['expires_in'] = f"{int(time_diff // 3600)} hours"
                            else:
                                model['expires_in'] = f"{int(time_diff // 86400)} days"
                        except Exception:
                            model['expires_in'] = 'Unknown'
                else:
                    model['expires_in'] = 'Never'
                rows.append(model)
        except Exception:
            continue
    return render_template('running_models.html', models=rows)

@app.route('/models/unload/<path:model_name>', methods=['POST'])
def unload_model(model_name):
    try:
        # Unload model by setting keep_alive to 0
        response = active_client().generate({"model": model_name, "keep_alive": 0}, stream=False)

        if response.status_code == 200:
            flash(f"Model {model_name} unloaded successfully", "success")
        else:
            flash(f"Error unloading model: {response.status_code}", "danger")
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")

    return redirect(url_for('running_models'))

@app.route('/chat')
def chat():
    # Get available models for the dropdown
    try:
        models_data = active_client().tags()
        return render_template('chat.html', models=models_data.get('models', []))
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")
        return render_template('chat.html', models=[])

@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.get_json()
    model = data.get('model')
    message = data.get('message')
    conversation = data.get('conversation', [])
    stream = data.get('stream', False)
    
    # Format message for Ollama API
    messages = conversation + [{"role": "user", "content": message}]
    
    if stream:
        return stream_chat_response(model, messages)

    try:
        response = active_client().chat(
            {"model": model, "messages": messages, "stream": False},
            stream=False
        )

        if response.status_code == 200:
            result = response.json()
            # Extract assistant's message from the response
            assistant_message = result.get('message', {}).get('content', '')
            return jsonify({
                "response": assistant_message,
                "conversation": messages + [{"role": "assistant", "content": assistant_message}]
            })
        else:
            return jsonify({"error": f"Error from Ollama API: {response.status_code}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error connecting to Ollama API: {str(e)}"}), 500

def stream_chat_response(model, messages):
    def generate():
        assistant_message = ""
        try:
            # Make streaming request to Ollama API
            response = active_client().chat(
                {"model": model, "messages": messages, "stream": True},
                stream=True
            )

            if response.status_code != 200:
                error_msg = f"Error from Ollama API: {response.status_code}"
                yield f"data: {json.dumps({'error': error_msg})}\n\n"
                return

            for line in response.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        if 'message' in chunk and 'content' in chunk['message']:
                            # Get the content delta
                            content_delta = chunk['message']['content']
                            assistant_message += content_delta
                            yield f"data: {json.dumps({'delta': content_delta, 'content': assistant_message})}\n\n"
                    except json.JSONDecodeError:
                        continue

            # Send the final message with the complete conversation
            final_conversation = messages + [{"role": "assistant", "content": assistant_message}]
            yield f"data: {json.dumps({'done': True, 'conversation': final_conversation})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return app.response_class(generate(), mimetype='text/event-stream')

@app.route('/generate')
def generate():
    # Get available models for the dropdown
    try:
        models_data = active_client().tags()
        return render_template('generate.html', models=models_data.get('models', []))
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")
        return render_template('generate.html', models=[])

@app.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json()
    model = data.get('model')
    prompt = data.get('prompt')
    system = data.get('system', '')
    options = data.get('options', {})
    
    # Build request payload
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }
    
    if system:
        payload["system"] = system
    
    # Convert string values to appropriate types for Ollama API
    if options:
        processed_options = {}
        for key, value in options.items():
            if key in ['num_ctx', 'num_predict', 'num_keep', 'seed', 'top_k']:
                # Convert to integer
                try:
                    processed_options[key] = int(value) if value != '' and value is not None else None
                except (ValueError, TypeError):
                    continue
            elif key in ['temperature', 'top_p', 'repeat_penalty', 'typical_p']:
                # Convert to float
                try:
                    processed_options[key] = float(value) if value != '' and value is not None else None
                except (ValueError, TypeError):
                    continue
            elif key in ['repeat_last_n']:
                # Convert to integer, handle -1 for no limit
                try:
                    processed_options[key] = int(value) if value != '' and value is not None else None
                except (ValueError, TypeError):
                    continue
            elif value != '' and value is not None:  # For other string options
                processed_options[key] = value
        
        # Only add options if we have valid ones
        if processed_options:
            payload["options"] = processed_options
    
    try:
        response = active_client().generate(payload, stream=False)

        if response.status_code == 200:
            result = response.json()
            return jsonify(result)
        else:
            return jsonify({"error": f"Error from Ollama API: {response.status_code}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error connecting to Ollama API: {str(e)}"}), 500

@app.route('/servers')
def servers_page():
    items = servers.list_servers()
    status = {}
    for s in items:
        status[s["id"]] = OllamaClient(s["base_url"]).ping()
    return render_template('servers.html', servers=items, status=status,
                           active_id=servers.get_active_id(session))


@app.route('/servers/add', methods=['POST'])
def servers_add():
    try:
        servers.add_server(request.form.get('name'), request.form.get('base_url'))
        flash("Server added", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for('servers_page'))


@app.route('/servers/<server_id>/edit', methods=['POST'])
def servers_edit(server_id):
    try:
        servers.update_server(
            server_id,
            name=request.form.get('name'),
            base_url=request.form.get('base_url'),
            enabled='enabled' in request.form,
        )
        flash("Server updated", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for('servers_page'))


@app.route('/servers/<server_id>/delete', methods=['POST'])
def servers_delete(server_id):
    try:
        servers.delete_server(server_id)
        flash("Server removed", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for('servers_page'))


@app.route('/servers/active', methods=['POST'])
def servers_set_active():
    try:
        servers.set_active(session, request.form.get('server_id'))
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(request.referrer or url_for('models'))


@app.context_processor
def inject_servers():
    return {
        "nav_servers": servers.get_enabled(),
        "nav_active_id": servers.get_active_id(session),
    }


@app.route('/help')
def help_page():
    return render_template('help.html')

@app.route('/model_help')
def model_help():
    return render_template('model_help.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/version')
def version():
    current_version = "Unknown"
    latest_version = "Unknown"
    update_available = False
    release_date = "Unknown"
    changelog_markdown = None

    # Get current version from Ollama API
    try:
        version_data = active_client().version()
        current_version = version_data.get('version', 'Unknown')
    except Exception as e:
        flash(f"Error connecting to Ollama API: {str(e)}", "danger")

    # Get latest version from GitHub releases
    try:
        github_response = requests.get("https://api.github.com/repos/ollama/ollama/releases/latest")
        if github_response.status_code == 200:
            github_data = github_response.json()
            latest_version = github_data.get('tag_name', 'Unknown')
            if latest_version.startswith('v'):
                latest_version = latest_version[1:]  # Remove 'v' prefix if present

            # Get release date
            if github_data.get('published_at'):
                from datetime import datetime
                try:
                    published_date = datetime.fromisoformat(github_data['published_at'].replace('Z', '+00:00'))
                    release_date = published_date.strftime('%B %d, %Y')
                except:
                    release_date = "Unknown"

            # Get raw markdown from release body and convert to HTML
            if github_data.get('body'):
                try:
                    # Convert markdown to HTML
                    changelog_markdown = markdown.markdown(github_data['body'], extensions=['extra'])
                except Exception:
                    # If conversion fails, use the raw markdown
                    changelog_markdown = github_data['body']

            # Check if update is available
            if current_version != "Unknown" and latest_version != "Unknown":
                current_parts = current_version.split('.')
                latest_parts = latest_version.split('.')

                # Compare version numbers
                for i in range(max(len(current_parts), len(latest_parts))):
                    current_num = int(current_parts[i]) if i < len(current_parts) else 0
                    latest_num = int(latest_parts[i]) if i < len(latest_parts) else 0

                    if latest_num > current_num:
                        update_available = True
                        break
                    elif current_num > latest_num:
                        break

        else:
            flash(f"Error fetching latest version from GitHub: {github_response.status_code}", "info")
    except Exception as e:
        flash(f"Error connecting to GitHub API: {str(e)}", "info")

    return render_template('version.html',
                          version=current_version,
                          latest_version=latest_version,
                          update_available=update_available,
                          release_date=release_date,
                          changelog_markdown=changelog_markdown)

# The parse_github_release_notes function is no longer used since we're displaying raw markdown

@app.route('/api/check-updates')
def check_updates():
    """API endpoint to check for available updates"""
    try:
        # Get current version
        current_version = "Unknown"
        try:
            version_data = active_client().version()
            current_version = version_data.get('version', 'Unknown')
        except Exception as e:
            return jsonify({"error": f"Error connecting to Ollama API: {str(e)}"}), 500
        
        # Get latest version from GitHub
        try:
            github_response = requests.get("https://api.github.com/repos/ollama/ollama/releases/latest")
            if github_response.status_code == 200:
                github_data = github_response.json()
                latest_version = github_data.get('tag_name', 'Unknown')
                if latest_version.startswith('v'):
                    latest_version = latest_version[1:]  # Remove 'v' prefix if present
                
                # Check if update is available
                update_available = False
                if current_version != "Unknown" and latest_version != "Unknown":
                    current_parts = current_version.split('.')
                    latest_parts = latest_version.split('.')
                    
                    # Compare version numbers
                    for i in range(max(len(current_parts), len(latest_parts))):
                        current_num = int(current_parts[i]) if i < len(current_parts) else 0
                        latest_num = int(latest_parts[i]) if i < len(latest_parts) else 0
                        
                        if latest_num > current_num:
                            update_available = True
                            break
                        elif current_num > latest_num:
                            break
                
                # Get release date
                release_date = "Unknown"
                if github_data.get('published_at'):
                    from datetime import datetime
                    try:
                        published_date = datetime.fromisoformat(github_data['published_at'].replace('Z', '+00:00'))
                        release_date = published_date.strftime('%B %d, %Y')
                    except:
                        pass
                
                # Get download URLs
                assets = github_data.get('assets', [])
                download_urls = {}
                for asset in assets:
                    name = asset.get('name', '')
                    if name.endswith('.dmg'):
                        download_urls['macos'] = asset.get('browser_download_url')
                    elif name.endswith('.msi'):
                        download_urls['windows'] = asset.get('browser_download_url')
                    elif 'linux' in name.lower() and name.endswith('.tar.gz'):
                        download_urls['linux'] = asset.get('browser_download_url')
                
                return jsonify({
                    "current_version": current_version,
                    "latest_version": latest_version,
                    "update_available": update_available,
                    "release_date": release_date,
                    "download_urls": download_urls,
                    "release_url": github_data.get('html_url')
                })
            else:
                return jsonify({"error": f"Error fetching GitHub data: {github_response.status_code}"}), 500
        except Exception as e:
            return jsonify({"error": f"Error checking for updates: {str(e)}"}), 500
    
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=True, threaded=True)
