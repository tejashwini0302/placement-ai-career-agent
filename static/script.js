document.getElementById('careerForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  const role = document.getElementById('role').value;
  const github = document.getElementById('github').value;

  // Demo values; replace with your FastAPI response later
  document.getElementById('atsScore').textContent = '89';
  document.getElementById('readinessScore').textContent = '84';
  document.getElementById('githubScore').textContent = github ? '82' : '75';

  document.getElementById('skillGap').innerHTML = `
    <span class="chip danger">Docker</span>
    <span class="chip danger">AWS</span>
    <span class="chip danger">System Design</span>
  `;

  document.getElementById('projects').innerHTML = `
    <li>AI Resume Analyzer</li>
    <li>Placement Tracker Dashboard</li>
    <li>Career Agent using LangChain</li>
  `;

  document.getElementById('jobs').innerHTML = `
    <div class="jobs-item">
      <h4>${role} — TCS</h4>
      <p>Hyderabad • Entry level</p>
    </div>
    <div class="jobs-item">
      <h4>${role} — Infosys</h4>
      <p>Bengaluru • Fresher</p>
    </div>
    <div class="jobs-item">
      <h4>${role} — Accenture</h4>
      <p>Pune • Campus hiring</p>
    </div>
  `;

  document.getElementById('githubEval').innerHTML = `
    <li>GitHub profile: ${github || 'Not provided'}</li>
    <li>Improve README and documentation</li>
    <li>Add deployment links and pinned repositories</li>
  `;
});
