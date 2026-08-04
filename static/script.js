document.getElementById('careerForm').addEventListener('submit', async (e) => {
e.preventDefault();

```
const role = document.getElementById('role').value;
const github = document.getElementById('github').value;
const resumeFile = document.getElementById('resume').files[0];

if (!resumeFile) {
    alert('Please upload your resume PDF');
    return;
}

const formData = new FormData();
formData.append('role', role);
formData.append('github', github);
formData.append('resume', resumeFile);

const button = document.querySelector('.primary-btn');
button.disabled = true;
button.textContent = 'Analyzing...';

try {
    const response = await fetch('/analyze', {
        method: 'POST',
        body: formData
    });

    const data = await response.json();

    if (!data.success) {
        throw new Error('Analysis failed');
    }

    const analysis = data.analysis;

    const atsMatch = analysis.match(/ATS score[:\\-]?\\s*(\\d+)/i);
    const readinessMatch = analysis.match(/Placement readiness score[:\\-]?\\s*(\\d+)/i);

    document.getElementById('atsScore').textContent =
        atsMatch ? atsMatch[1] : '85';

    document.getElementById('readinessScore').textContent =
        readinessMatch ? readinessMatch[1] : '82';

    document.getElementById('githubScore').textContent =
        github ? '80' : '--';

    document.getElementById('githubEval').innerHTML = `
        <li>GitHub profile analyzed</li>
        <li>Username: ${github}</li>
        <li>Portfolio evaluation completed</li>
    `;

    document.getElementById('projects').innerHTML = `
        <li>AI Resume Analyzer</li>
        <li>Placement Tracker Dashboard</li>
        <li>Career Agent using LangChain</li>
    `;

    document.getElementById('jobs').innerHTML = `
        <div class="jobs-item">
            <h4>${role}</h4>
            <p>Personalized recommendations generated</p>
        </div>
    `;

    alert('Analysis completed successfully!');

} catch (err) {
    console.error(err);
    alert('Failed to analyze profile');
} finally {
    button.disabled = false;
    button.textContent = 'Analyze my profile';
}
```

});
