"""
Tenant-specific UI Theme Loader for multi-tenant template overrides.
Allows each organization to override any template file or design element
while preserving identical backend routing and logic.
"""

import os
import weakref
import jinja2
from flask import g


class TenantThemeJinjaLoader(jinja2.BaseLoader):
    """
    Dynamic Jinja2 Template Loader supporting per-tenant theme directory overrides.

    Resolution order:
    1. If the active tenant (g.organization) has a `theme` configured:
       First check if the requested template exists in `app/templates/themes/<theme_name>/<template>`.
    2. If not found in the custom theme directory (or if no custom theme is set),
       fall back to the application's standard base template loader.
    """

    def __init__(self, app, base_loader):
        self.app = app
        self.base_loader = base_loader

    def _get_theme_dirs(self):
        org = getattr(g, "organization", None)
        if not org or not getattr(org, "theme", None):
            return []

        # Enforce SaaS subscription feature entitlement: custom themes require 'custom_themes' entitlement
        if hasattr(org, "has_feature") and not org.has_feature("custom_themes"):
            return []

        theme_name = org.theme.strip()
        dirs = []

        # 1. Standard theme directory: app/templates/themes/<theme_name>
        rel_theme_path = os.path.join(self.app.root_path, "templates", "themes", theme_name)
        if os.path.isdir(rel_theme_path):
            dirs.append(rel_theme_path)

        # 2. Custom relative or absolute directory path
        if os.path.isdir(theme_name):
            dirs.append(theme_name)
        else:
            abs_candidate = os.path.abspath(theme_name)
            if os.path.isdir(abs_candidate):
                dirs.append(abs_candidate)

        return dirs

    def get_source(self, environment, template):
        # 1. Check custom tenant theme directories first
        for theme_dir in self._get_theme_dirs():
            theme_file = os.path.join(theme_dir, template)
            if os.path.isfile(theme_file):
                loader = jinja2.FileSystemLoader(theme_dir)
                try:
                    return loader.get_source(environment, template)
                except jinja2.TemplateNotFound:
                    pass

        # 2. Fallback to default base template loader
        return self.base_loader.get_source(environment, template)

    def list_templates(self):
        return self.base_loader.list_templates()


def setup_tenant_theme_loader(app):
    """
    Hooks the dynamic TenantThemeJinjaLoader into Flask's Jinja environment and
    scopes Jinja's bytecode/template cache by tenant theme to prevent cross-tenant UI bleed.
    """
    app.jinja_env.loader = TenantThemeJinjaLoader(app, app.jinja_env.loader)

    def tenant_load_template(name, globals=None):
        org = getattr(g, "organization", None)
        has_custom_theme = bool(
            org and getattr(org, "theme", None) and (not hasattr(org, "has_feature") or org.has_feature("custom_themes"))
        )
        theme = (org.theme or "default").strip() if has_custom_theme else "default"
        cache_key = (weakref.ref(app.jinja_env.loader), theme, name)

        if app.jinja_env.cache is not None:
            template = app.jinja_env.cache.get(cache_key)
            if template is not None and (not app.jinja_env.auto_reload or template.is_up_to_date):
                if globals:
                    template.globals.update(globals)
                return template

        template = app.jinja_env.loader.load(app.jinja_env, name, app.jinja_env.make_globals(globals))
        if app.jinja_env.cache is not None:
            app.jinja_env.cache[cache_key] = template
        return template

    app.jinja_env._load_template = tenant_load_template
