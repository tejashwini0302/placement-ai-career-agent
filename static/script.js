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

    // Show the analysis on the page instead of an alert
    document.getElementById('githubEval').innerHTML =
        `<li>${data.analysis.replace(/\\n/g, '<br>')}</li>`;

    document.getElementById('atsScore').textContent = 'AI';
    document.getElementById('readinessScore').textContent = 'AI';
    document.getElementById('githubScore').textContent = github ? 'AI' : '--';

} catch (err) {
    console.error(err);
    alert('Failed to analyze profile');
} finally {
    button.disabled = false;
    button.textContent = 'Analyze my profile';
}
```

});
