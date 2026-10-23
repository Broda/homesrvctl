import pytest
from homesrvctl.templates import render_template

@pytest.mark.parametrize("template,stage", [("node/Dockerfile.j2", ""), ("rust-react-postgres/frontend.Dockerfile.j2", " AS build")])
def test_node_security_base_image(template, stage):
    rendered = render_template("app/" + template, {"ports": {"app": 3000}})
    assert rendered.splitlines()[0] == "FROM node:22.23.4-alpine" + stage
    if stage:
        assert "FROM nginx:alpine" in rendered
        assert "RUN npm run build" in rendered
    else:
        assert 'CMD ["npm", "start"]' in rendered
