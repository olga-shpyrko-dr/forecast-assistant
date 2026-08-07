# Copyright 2024 DataRobot, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys

import streamlit as st

sys.path.append("..")

from common import app_settings, render_sidebar, set_title

sys.setrecursionlimit(10000)

# Configure the page title, favicon, layout, etc
st.set_page_config(
    page_title=app_settings.page_title,
    layout="wide",
    page_icon="./datarobot_favicon.png",
)

with open("./style.css") as f:
    css = f.read()

st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

hide_streamlit_style = """
<style>
# MainMenu {visibility: hidden;}
header {visibility: hidden;}
footer {visibility: hidden;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

set_title()

pg = st.navigation(
    [
        st.Page("views/forecast.py", title="Forecast", default=True),
        st.Page("views/whatif.py", title="What-If Scenarios"),
    ]
)

render_sidebar()

pg.run()
