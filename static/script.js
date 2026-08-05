document.getElementById('careerForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    const role = document.getElementById('role').value;
    const github = document.getElementById('github').value;
    const resume = document.getElementById('resume').files[0];

    if (!resume) {
        alert('Please upload your resume PDF');
        return;
    }

    const formData = new FormData();
    formData.append('role', role);
    formData.append('github', github);
    formData.append('resume', resume);

    const button = document.querySelector('.primary-btn');
    button.disabled = true;
    button.textContent = 'Analyzing...';

    try {
        const response = await fetch('/analyze', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        document.getElementById('atsScore').textContent = data.ats_score;
        document.getElementById('readinessScore').textContent = data.placement_readiness;
        document.getElementById('githubScore').textContent = data.github_score;

        document.getElementById('skillGap').innerHTML =
            data.missing_skills.map(skill =>
                `<span class="chip danger">${skill}</span>`
            ).join('');

        document.getElementById('githubEval').innerHTML =
            data.github_recommendations.map(item =>
                `<li>${item}</li>`
            ).join('');

        document.getElementById('projects').innerHTML =
            data.projects.map(project =>
                `<li>${project}</li>`
            ).join('');

        document.getElementById('jobs').innerHTML =
            data.jobs.map(job =>
                `<div class="jobs-item">
                    <h4>${job.title}</h4>
                    <a href="${job.url}" target="_blank">Apply</a>
                 </div>`
            ).join('');

    } catch (err) {
        console.error(err);
        alert('Analysis failed');
    } finally {
        button.disabled = false;
        button.textContent = 'Analyze my profile';
    }
});
