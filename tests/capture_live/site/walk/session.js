// The panel's session: a persistent cookie (seeded by ../session.html?start); none means the login page.
if (!document.cookie.includes('sid=ok')) location.replace('login.html');
